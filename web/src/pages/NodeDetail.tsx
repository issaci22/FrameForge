import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Check, Pause, Play, RefreshCw, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { useAction, useNode } from "../api/hooks";
import type { Capabilities, Node, NodeConstraints, NodeEnrollment, PathMapping } from "../api/types";
import { Meter, Sparkline } from "../components/media";
import { Callout, ConfirmButton, CopyBlock, ErrorState, LoadingState, Modal, NumberInput, PageHeader, Section, SettingRow, Tabs, Toggle, useToast } from "../components/ui";
import { seedNodeHistory, useLive } from "../live/events";
import { ago, rate } from "../lib/format";
import { PathMappingsEditor, ServerUrlWarning, accelSummary } from "./Nodes";

const CODECS = ["h264", "hevc", "av1"] as const;
const CODEC_NAMES = { h264: "H.264", hevc: "H.265", av1: "AV1" };
const BACKENDS = ["nvenc", "qsv", "vaapi", "amf", "cpu"] as const;
const BACKEND_NAMES: Record<string, string> = { nvenc: "NVIDIA NVENC", qsv: "Intel QSV", vaapi: "VA-API", amf: "AMD AMF", cpu: "CPU" };
const STATUS_TEXT: Record<string, string> = { online: "Online", offline: "Offline", pending: "Waiting for first connection", disabled: "Disabled", paused: "Paused" };

type Tab = "overview" | "hardware" | "settings" | "connection" | "log";

export function NodeDetailPage() {
  const id = Number(useParams().id);
  const { data: node, error, refetch } = useNode(id);
  const live = useLive((s) => s.metrics[id]);
  const toast = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [enroll, setEnroll] = useState<NodeEnrollment | null>(null);
  const patch = useAction((body: Record<string, unknown>) => api.patch<Node>(`/nodes/${id}`, body), [["nodes"], ["node", id]]);
  const redetect = useAction(() => api.post(`/nodes/${id}/redetect`), []);
  const regen = useAction(() => api.post<NodeEnrollment>(`/nodes/${id}/token`), [["node", id]]);
  const del = useAction(() => api.del(`/nodes/${id}`), [["nodes"]]);
  const { data: logs, refetch: refetchLogs, isFetching: logsFetching } = useQuery({ queryKey: ["node-logs", id], queryFn: () => api.text(`/nodes/${id}/logs?lines=300`) });

  useEffect(() => {
    if (node?.history?.length) seedNodeHistory(id, node.history);
  }, [id, node?.history]);

  if (error && !node)
    return (
      <div className="page">
        <PageHeader title="Node not found" back={{ to: "/nodes", label: "Nodes" }} />
        <ErrorState title="This node doesn't exist or couldn't be loaded" error={error} onRetry={() => refetch()} />
      </div>
    );
  if (!node) return <div className="page"><LoadingState /></div>;

  const m = live?.metrics ?? node.metrics;
  const hist = live?.history ?? node.history ?? [];
  const caps = node.capabilities;
  const gpu = m?.gpus?.[0];
  const onErr = (e: Error) => toast(e.message, "err");
  const tabs: { value: Tab; label: string }[] = [
    { value: "overview", label: "Overview" },
    { value: "hardware", label: "Hardware" },
    { value: "settings", label: "Settings" },
    ...(!node.is_local ? [{ value: "connection" as Tab, label: "Connection" }] : []),
    { value: "log", label: "Log" },
  ];
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = requested && tabs.some((t) => t.value === requested) ? requested : "overview";
  const setTab = (t: Tab) => {
    const next = new URLSearchParams(params);
    if (t === "overview") next.delete("tab");
    else next.set("tab", t);
    setParams(next, { replace: true });
  };

  return (
    <div className="page">
      <PageHeader
        back={{ to: "/nodes", label: "Nodes" }}
        prefix={<span className={`status-dot ${node.status}`} />}
        title={node.name}
        description={
          <>
            {STATUS_TEXT[node.status] ?? node.status}
            {node.online ? ` · ${accelSummary(node).join(", ") || "CPU encoding"}` : node.status !== "pending" ? ` · last seen ${ago(node.last_seen_at)}` : ""}
            {node.is_local && " · built into the server"}
          </>
        }
        actions={
          <>
            {node.online && (
              <button className="btn" onClick={() => redetect.mutate(undefined, { onSuccess: () => toast("Re-detecting hardware…"), onError: onErr })}>
                <RefreshCw size={15} /> Re-detect hardware
              </button>
            )}
            <button className="btn" onClick={() => patch.mutate({ paused: !node.paused }, { onError: onErr })}>
              {node.paused ? <Play size={15} /> : <Pause size={15} />} {node.paused ? "Resume" : "Pause"}
            </button>
          </>
        }
      />

      {node.status === "pending" && (
        <div className="mb-section">
          <Callout kind="info" title="This node hasn't connected yet">
            Start the node container with the token you were given. Lost it? Regenerate a token on the{" "}
            <button type="button" className="link-btn" onClick={() => setTab("connection")}>
              Connection
            </button>{" "}
            tab.
          </Callout>
        </div>
      )}

      <Tabs value={tab} onChange={setTab} tabs={tabs} />

      {tab === "overview" && (
        <div className="grid-main-side">
          <Section title="Live" actions={<span className="faint small mono">{(live?.active ?? node.active_jobs).length} / {node.max_concurrency} jobs</span>}>
            {node.online && m ? (
              <div className="grid-2 live-grid">
                <div className="stack gap-3">
                  <Meter name="CPU" value={m.cpu_percent} />
                  <Meter name="RAM" value={(m.ram_used_mb / m.ram_total_mb) * 100} label={`${(m.ram_used_mb / 1024).toFixed(1)}/${(m.ram_total_mb / 1024).toFixed(0)} GB`} />
                  {gpu && (
                    <>
                      <Meter name="GPU" value={gpu.utilization} />
                      <Meter name="ENC" value={gpu.encoder_utilization} />
                      <Meter
                        name="VRAM"
                        value={gpu.vram_used_mb != null && gpu.vram_total_mb ? (gpu.vram_used_mb / gpu.vram_total_mb) * 100 : null}
                        label={gpu.vram_used_mb != null && gpu.vram_total_mb ? `${(gpu.vram_used_mb / 1024).toFixed(1)}/${(gpu.vram_total_mb / 1024).toFixed(0)} GB` : undefined}
                      />
                    </>
                  )}
                  <div className="faint small mono io-line">
                    disk r {rate(m.disk_read_bps)} · w {rate(m.disk_write_bps)} · net ↓ {rate(m.net_rx_bps)} ↑ {rate(m.net_tx_bps)}
                  </div>
                </div>
                <div className="stack gap-4">
                  <div>
                    <div className="faint small">CPU · last {Math.round((hist.length * 2) / 60)} min</div>
                    <Sparkline points={hist} keyName="cpu" />
                  </div>
                  <div>
                    <div className="faint small">GPU</div>
                    <Sparkline points={hist} keyName="gpu" color="var(--c-hevc)" />
                  </div>
                </div>
              </div>
            ) : (
              <span className="muted">{node.status === "pending" ? "No data until the node connects." : "Node is offline."}</span>
            )}
          </Section>

          <MachineInfo node={node} />
        </div>
      )}

      {tab === "hardware" && (
        <div className="grid-main-side">
          <Section title="Encoders" description="Verified with a real test encode when the node starts.">
            {caps ? (
              <div className="stack">
                <div className="table-scroll">
                  <EncoderMatrix caps={caps} />
                </div>
                {caps.decoders.length > 0 && (
                  <div className="small">
                    <span className="faint">Hardware decoding: </span>
                    {caps.decoders.filter((d) => d.verified).map((d) => `${d.backend.toUpperCase()} ${d.codec}`).join(", ") || "none verified"}
                  </div>
                )}
                {caps.notes.map((n) => (
                  <Callout key={n} kind="info">
                    {n}
                  </Callout>
                ))}
              </div>
            ) : (
              <span className="muted">No capability report yet.</span>
            )}
          </Section>
          <MachineInfo node={node} />
        </div>
      )}

      {tab === "settings" && <NodeSettings node={node} onSave={(body) => patch.mutate(body, { onSuccess: () => toast("Node settings saved"), onError: onErr })} />}

      {tab === "connection" && !node.is_local && (
        <div className="stack gap-6 narrow-col">
          <Section title="Connection token" description="The node authenticates with this token. It is only shown when it's created.">
            <div className="settings-list">
              <SettingRow label="Current token" description="Regenerating disconnects the node until you update its FF_NODE_TOKEN.">
                <span className="mono small">{node.token_hint}…</span>
                <ConfirmButton
                  className="btn"
                  confirmText="Disconnect this node and create a new token?"
                  detail="The node stays offline until its container uses the new token."
                  confirmLabel="Regenerate"
                  onConfirm={() => regen.mutate(undefined, { onSuccess: setEnroll, onError: onErr })}
                >
                  Regenerate token
                </ConfirmButton>
              </SettingRow>
            </div>
          </Section>
          <Section title="Remove node" className="danger-zone">
            <div className="settings-list">
              <SettingRow label="Remove this node from FrameForge" description="Only possible while it runs no jobs. The machine and its container aren't changed.">
                <ConfirmButton
                  className="btn danger"
                  confirmText={`Remove “${node.name}”?`}
                  detail="It is disconnected, and its token stops working."
                  confirmLabel="Remove"
                  onConfirm={() => del.mutate(undefined, { onSuccess: () => navigate("/nodes"), onError: onErr })}
                >
                  <Trash2 size={14} /> Remove node
                </ConfirmButton>
              </SettingRow>
            </div>
          </Section>
        </div>
      )}

      {tab === "log" && (
        <Section
          title="Node log"
          actions={
            <button className="btn sm" onClick={() => refetchLogs()} disabled={logsFetching}>
              <RefreshCw size={14} className={logsFetching ? "spin" : undefined} /> Refresh
            </button>
          }
        >
          <pre className="log tall">{logs || "No log lines yet."}</pre>
        </Section>
      )}

      {enroll && (
        <Modal wide title="New connection details" subtitle="Update the node's container with this token." onClose={() => setEnroll(null)}>
          <div className="stack">
            <ServerUrlWarning url={enroll.server_url} />
            <CopyBlock text={enroll.compose} />
          </div>
        </Modal>
      )}
    </div>
  );
}

function MachineInfo({ node }: { node: Node }) {
  const caps = node.capabilities;
  return (
    <Section title="Machine">
      {caps ? (
        <dl className="kv">
          <dt>CPU</dt>
          <dd>
            {caps.cpu_model} ({caps.cpu_threads} threads)
          </dd>
          <dt>Memory</dt>
          <dd>{(caps.ram_total_mb / 1024).toFixed(1)} GB</dd>
          {caps.gpus.map((g) => (
            <FragmentKV key={g.index} k={`GPU ${g.index}`} v={`${g.name}${g.vram_total_mb ? ` · ${(g.vram_total_mb / 1024).toFixed(0)} GB` : ""}${g.driver ? ` · ${g.driver}` : ""}`} />
          ))}
          {caps.gpus.length === 0 && <FragmentKV k="GPU" v="None visible in the container" />}
          <dt>OS</dt>
          <dd>{caps.os}</dd>
          <dt>FFmpeg</dt>
          <dd className="small">{caps.ffmpeg_version ?? "not found"}</dd>
          <dt>HandBrake</dt>
          <dd className="faint">Not available in this version</dd>
          <dt>Node version</dt>
          <dd>{node.version ?? "—"}</dd>
        </dl>
      ) : (
        <span className="muted small">Hardware is reported when the node first connects.</span>
      )}
    </Section>
  );
}

function FragmentKV({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}

/** Codec × backend grid of the encoders a node verified with a real test encode. */
export function EncoderMatrix({ caps }: { caps: Capabilities }) {
  return (
    <div className="enc-matrix">
      <div className="head" />
      {CODECS.map((c) => (
        <div key={c} className="head">
          {CODEC_NAMES[c]}
        </div>
      ))}
      {BACKENDS.filter((b) => b === "cpu" || caps.encoders.some((e) => e.backend === b)).map((b) => (
        <EncoderRow key={b} backend={b} caps={caps} />
      ))}
    </div>
  );
}

function EncoderRow({ backend, caps }: { backend: string; caps: Capabilities }) {
  const encs = caps.encoders;
  return (
    <>
      <div className="backend">{BACKEND_NAMES[backend]}</div>
      {CODECS.map((c) => {
        const e = encs.find((x) => x.backend === backend && x.codec === c);
        if (!e) return <div key={c} className="cap-no small">not in build</div>;
        return (
          <div key={c} className={e.verified ? "cap-yes" : "cap-no"} title={e.error ?? e.name}>
            {e.verified ? <Check size={14} /> : <X size={14} />}
            <span className="mono small ellipsis">{e.verified ? e.name : e.error ?? "failed"}</span>
          </div>
        );
      })}
    </>
  );
}

function NodeSettings({ node, onSave }: { node: Node; onSave: (body: Record<string, unknown>) => void }) {
  const [concurrency, setConcurrency] = useState<number | null>(node.max_concurrency);
  const [reserve, setReserve] = useState(node.reserve_slot_for_normal);
  const [c, setC] = useState<NodeConstraints>(node.constraints ?? {});
  const [windowOn, setWindowOn] = useState(!!node.constraints?.window);
  const [mappings, setMappings] = useState<PathMapping[]>(node.path_mappings ?? []);
  const [enabled, setEnabled] = useState(node.enabled);
  const rec = node.capabilities?.recommended_concurrency;

  return (
    <div className="stack gap-6 narrow-col">
      <Section title="Workload">
        <div className="settings-list">
          <SettingRow label="Node enabled" description="A disabled node gets no new jobs.">
            <Toggle checked={enabled} onChange={setEnabled} />
          </SettingRow>
          <SettingRow label="Jobs at the same time" description={rec ? `Recommended for this hardware: ${rec}. More jobs don't always mean more speed.` : "More jobs don't always mean more speed."}>
            <div className="w-num-sm">
              <NumberInput value={concurrency} onChange={setConcurrency} min={1} max={16} />
            </div>
          </SettingRow>
          <SettingRow label="Keep one slot free" description="Reserve a slot for Normal or higher priority jobs, so background work never blocks them.">
            <Toggle checked={reserve} onChange={setReserve} />
          </SettingRow>
        </div>
      </Section>

      <Section title="When to start jobs" description="Leave this machine alone while you use it. The load limits are checked while this node runs no FrameForge jobs.">
        <div className="settings-list">
          <SettingRow label="Don't start if GPU above" description="e.g. while you're gaming or editing">
            <div className="w-num-sm">
              <NumberInput value={c.max_gpu_util ?? null} allowEmpty onChange={(v) => setC({ ...c, max_gpu_util: v })} min={1} max={100} suffix="%" placeholder="off" />
            </div>
          </SettingRow>
          <SettingRow label="Don't start if CPU above">
            <div className="w-num-sm">
              <NumberInput value={c.max_cpu_util ?? null} allowEmpty onChange={(v) => setC({ ...c, max_cpu_util: v })} min={1} max={100} suffix="%" placeholder="off" />
            </div>
          </SettingRow>
          <SettingRow label="Only work during certain hours" dim={!windowOn}>
            <Toggle checked={windowOn} onChange={setWindowOn} />
            <input className="input mono w-time" type="time" aria-label="Start" disabled={!windowOn} value={c.window?.start ?? "23:00"} onChange={(e) => setC({ ...c, window: { ...c.window, start: e.target.value, end: c.window?.end ?? "07:00" } })} />
            <span className="faint">to</span>
            <input className="input mono w-time" type="time" aria-label="End" disabled={!windowOn} value={c.window?.end ?? "07:00"} onChange={(e) => setC({ ...c, window: { ...c.window, start: c.window?.start ?? "23:00", end: e.target.value } })} />
          </SettingRow>
        </div>
      </Section>

      <Section title="Path mappings" description="Server path → path on this node, when this machine mounts the media somewhere else.">
        <PathMappingsEditor value={mappings} onChange={setMappings} />
      </Section>

      <div className="row end">
        <button
          className="btn primary"
          onClick={() =>
            onSave({
              max_concurrency: concurrency ?? 1,
              reserve_slot_for_normal: reserve,
              constraints: {
                max_gpu_util: c.max_gpu_util ?? null,
                max_cpu_util: c.max_cpu_util ?? null,
                window: windowOn ? { start: c.window?.start ?? "23:00", end: c.window?.end ?? "07:00", days: c.window?.days ?? [0, 1, 2, 3, 4, 5, 6] } : null,
              },
              path_mappings: mappings.filter((m) => m.server && m.node),
              enabled,
            })
          }
        >
          Save settings
        </button>
      </div>
    </div>
  );
}
