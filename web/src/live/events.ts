// Live updates over /api/v1/events. A tiny external store keeps high-frequency data
// (progress, metrics, scan state) out of React Query; structural changes invalidate queries.

import { useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { CompressionPreview, Job, MetricPoint, NodeMetrics, ScanState } from "../api/types";

export interface LiveProgress {
  percent: number;
  frame: number | null;
  fps: number | null;
  speed: number | null;
  bitrate_kbps: number | null;
  out_size: number | null;
  elapsed: number;
  eta: number | null;
  gpu_util: number | null;
  node_id: number;
  at: number;
}

interface LiveState {
  connected: boolean;
  progress: Record<number, LiveProgress>;
  metrics: Record<number, { metrics: NodeMetrics | null; active: number[]; history: MetricPoint[] }>;
  scans: Record<number, ScanState & { active: boolean }>;
  logs: Record<number, string[]>;
  logSeq: Record<number, number>; // total lines ever received per job
  previews: Record<string, CompressionPreview>;
  version: number;
}

const HISTORY_LIMIT = 900;
const LOG_LIMIT = 400;

let state: LiveState = { connected: false, progress: {}, metrics: {}, scans: {}, logs: {}, logSeq: {}, previews: {}, version: 0 };
const listeners = new Set<() => void>();

function emit(next: Partial<LiveState>) {
  state = { ...state, ...next, version: state.version + 1 };
  listeners.forEach((l) => l());
}

export function useLive<T>(selector: (s: LiveState) => T): T {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => selector(state),
  );
}

/** Seed a compression preview from an API response; later changes arrive as preview.updated events. */
export function putPreview(p: CompressionPreview) {
  emit({ previews: { ...state.previews, [p.id]: p } });
}

export function logSeq(jobId: number): number {
  return state.logSeq[jobId] ?? 0;
}

export function seedNodeHistory(nodeId: number, history: MetricPoint[]) {
  const cur = state.metrics[nodeId];
  if (cur && cur.history.length >= history.length) return;
  emit({ metrics: { ...state.metrics, [nodeId]: { metrics: cur?.metrics ?? null, active: cur?.active ?? [], history: history.slice(-HISTORY_LIMIT) } } });
}

type EventMsg = { topic: string; data: Record<string, unknown>; ts: number };

let socket: WebSocket | null = null;
let stopped = false;
let retry = 1000;
let invalidateTimer: number | null = null;
const pendingInvalidations = new Set<string>();

function scheduleInvalidate(qc: QueryClient, ...roots: string[]) {
  roots.forEach((r) => pendingInvalidations.add(r));
  if (invalidateTimer != null) return;
  invalidateTimer = window.setTimeout(() => {
    invalidateTimer = null;
    const roots = [...pendingInvalidations];
    pendingInvalidations.clear();
    for (const root of roots) qc.invalidateQueries({ queryKey: [root] });
  }, 400);
}

function patchJobInLists(qc: QueryClient, job: Job) {
  qc.setQueriesData<{ items: Job[] } | undefined>({ queryKey: ["jobs"] }, (old) => {
    if (!old || !Array.isArray(old.items)) return old;
    let found = false;
    const items = old.items.map((j) => {
      if (j.id === job.id) {
        found = true;
        return { ...j, ...job };
      }
      return j;
    });
    return found ? { ...old, items } : old;
  });
}

function handle(qc: QueryClient, msg: EventMsg) {
  const d = msg.data;
  switch (msg.topic) {
    case "job.progress": {
      const id = d.id as number;
      emit({
        progress: {
          ...state.progress,
          [id]: {
            percent: (d.percent as number) ?? 0,
            frame: (d.frame as number) ?? null,
            fps: (d.fps as number) ?? null,
            speed: (d.speed as number) ?? null,
            bitrate_kbps: (d.bitrate_kbps as number) ?? null,
            out_size: (d.out_size as number) ?? null,
            elapsed: (d.elapsed as number) ?? 0,
            eta: (d.eta as number) ?? null,
            gpu_util: (d.gpu_util as number) ?? null,
            node_id: d.node_id as number,
            at: Date.now(),
          },
        },
      });
      break;
    }
    case "job.updated":
    case "job.created": {
      const job = d as unknown as Job;
      patchJobInLists(qc, job);
      if (["completed", "failed", "cancelled", "queued"].includes(job.state)) {
        const { [job.id]: _drop, ...rest } = state.progress;
        void _drop;
        emit({ progress: rest });
      }
      qc.invalidateQueries({ queryKey: ["job", job.id] });
      scheduleInvalidate(qc, "jobs", "stats", "files", "file", "libraries", "nodes");
      break;
    }
    case "job.log": {
      const id = d.id as number;
      const incoming = (d.lines as string[]) ?? [];
      const lines = [...(state.logs[id] ?? []), ...incoming].slice(-LOG_LIMIT);
      emit({ logs: { ...state.logs, [id]: lines }, logSeq: { ...state.logSeq, [id]: (state.logSeq[id] ?? 0) + incoming.length } });
      break;
    }
    case "node.metrics": {
      const id = d.id as number;
      const prev = state.metrics[id];
      const history = [...(prev?.history ?? []), d.point as MetricPoint].slice(-HISTORY_LIMIT);
      emit({ metrics: { ...state.metrics, [id]: { metrics: d.metrics as NodeMetrics, active: (d.active_jobs as number[]) ?? [], history } } });
      break;
    }
    case "node.status":
    case "node.updated":
      scheduleInvalidate(qc, "nodes", "node", "stats", "profiles");
      break;
    case "preview.updated":
      putPreview(d as unknown as CompressionPreview);
      break;
    case "retention.updated":
      scheduleInvalidate(qc, "retention", "libraries");
      break;
    case "library.scan": {
      const id = d.library_id as number;
      const phase = d.phase as string;
      emit({ scans: { ...state.scans, [id]: { ...(d as ScanState), active: phase !== "done" } } });
      if (phase === "done") scheduleInvalidate(qc, "libraries", "files", "jobs", "stats");
      break;
    }
    case "file.updated":
      scheduleInvalidate(qc, "files", "file");
      break;
    case "stats.changed":
      scheduleInvalidate(qc, "stats");
      break;
  }
}

export function connectLive(qc: QueryClient) {
  if (socket) return;
  stopped = false;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/v1/events`);
  socket = ws;
  ws.onopen = () => {
    retry = 1000;
    emit({ connected: true });
    // Catch up on anything missed while disconnected.
    qc.invalidateQueries();
  };
  ws.onmessage = (ev) => {
    try {
      handle(qc, JSON.parse(ev.data) as EventMsg);
    } catch {
      /* ignore malformed frames */
    }
  };
  ws.onclose = (ev) => {
    socket = null;
    emit({ connected: false });
    if (stopped || ev.code === 4401) return; // signed out
    window.setTimeout(() => connectLive(qc), retry);
    retry = Math.min(retry * 2, 15000);
  };
}

export function disconnectLive() {
  stopped = true;
  socket?.close();
  socket = null;
}
