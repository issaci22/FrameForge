import { useMemo, useState } from "react";
import type { Node, TimeWindow } from "../../api/types";
import { Field, NumberInput, Segmented, Toggle } from "../../components/ui";
import { PRIORITIES } from "../../lib/format";
import { ALL_DAYS, DAY_NAMES, effectiveQuietHours, inWindow, nodeDraft, OVERNIGHT, type Issue, type NodeDraft, type QuietHours, type SetupDraft } from "./model";
import { GuideSection, issueProps, Panel, StepFrame, WindowEditor, type StepProps } from "./parts";

interface JobClass {
  key: string;
  label: string;
  priority: number;
  ruleWindow: TimeWindow | null;
  manual: boolean;
}

type Block = "rule" | "quiet" | "nodes";
const BLOCK_TEXT: Record<Block, string> = { rule: "outside the aging-policy window", quiet: "background work hours", nodes: "no node working" };
const SAMPLE_MINUTES = 5;
// Kept muted: a fully open week would otherwise be a wall of accent color.
const GRID_FILL_PCT = 45;

/** Mirrors the scheduler's window checks (services/scheduler.py): rule window, quiet hours, node working hours. */
function blockAt(cls: JobClass, day: number, minute: number, quiet: QuietHours | null, nodeWindows: (TimeWindow | null)[]): Block | null {
  if (!cls.manual && cls.ruleWindow && !inWindow(cls.ruleWindow, day, minute)) return "rule";
  if (quiet?.enabled && !cls.manual) {
    const limit = quiet.applies_to === "background" ? 0 : 1;
    if (cls.priority <= limit && !inWindow(quiet.window, day, minute)) return "quiet";
  }
  if (!nodeWindows.some((w) => inWindow(w, day, minute))) return "nodes";
  return null;
}

function jobClasses(d: SetupDraft): JobClass[] {
  const out: JobClass[] = [];
  if (d.aging.enabled) {
    for (const p of [...new Set(d.aging.stages.filter((s) => s.action === "transcode").map((s) => s.priority))].sort()) {
      out.push({ key: `aging-${p}`, label: `Aging jobs · ${PRIORITIES[p]}`, priority: p, ruleWindow: d.policyWindow, manual: false });
    }
  }
  for (const p of [...new Set(d.exceptions.filter((e) => e.action === "transcode").map((e) => e.priority))].sort()) {
    out.push({ key: `rule-${p}`, label: `Other rules · ${PRIORITIES[p]}`, priority: p, ruleWindow: null, manual: false });
  }
  out.push({ key: "manual", label: "Manual jobs", priority: 3, ruleWindow: null, manual: true });
  return out;
}

export function ScheduleStep({ draft, update, server, issues, showErrors }: StepProps) {
  const nodes = server.nodes ?? [];
  const quiet = effectiveQuietHours(draft, server.settings);
  const setQuiet = (patch: Partial<QuietHours>) => quiet && update((d) => ({ ...d, quietHours: { ...quiet, ...patch } }));
  const setNode = (n: Node, patch: Partial<NodeDraft>) => update((d) => ({ ...d, nodes: { ...d.nodes, [n.id]: { ...nodeDraft(d, n), ...patch } } }));
  const quietLimited = quiet?.applies_to === "low_and_below" ? 1 : 0;
  const agingPriorities = draft.aging.stages.filter((s) => s.action === "transcode").map((s) => s.priority);

  return (
    <StepFrame
      step="schedule"
      title="Choose when work can run"
      lede="Keep transcoding out of your way: hold background work for the night, or stop a node from starting jobs while you're gaming or editing. Windows only control starting; a running job always finishes. Times use the container's time zone (TZ)."
      guide={<ScheduleGuide />}
    >
      <Panel title="When work can start" description="A week at a glance for each kind of job. It follows the settings below as you change them.">
        <WeekGrid draft={draft} quiet={quiet} nodes={nodes} />
      </Panel>

      <Panel
        title="Background work hours"
        description="Holds back low-priority automatic jobs on every node until these hours. Jobs you queue by hand always start right away."
        right={quiet && <Toggle checked={quiet.enabled} onChange={(v) => setQuiet({ enabled: v })} label={quiet.enabled ? "On" : "Off"} />}
      >
        {!quiet ? (
          <div className="faint small">Loading settings…</div>
        ) : (
          <>
            <div className="row wrap" style={{ opacity: quiet.enabled ? 1 : 0.55 }}>
              <span className="small muted">Run</span>
              <Segmented
                value={quiet.applies_to}
                onChange={(v) => setQuiet({ applies_to: v })}
                options={[
                  { value: "background", label: "Background jobs" },
                  { value: "low_and_below", label: "Low + Background jobs" },
                ]}
              />
              <span className="small muted">only during</span>
            </div>
            <Field {...issueProps(issues, "quiet", showErrors)}>
              <WindowEditor value={quiet.window} disabled={!quiet.enabled} label="Background work hours" onChange={(w) => setQuiet({ window: { ...w, days: w.days ?? ALL_DAYS } })} />
            </Field>
            {quiet.enabled && agingPriorities.some((pr) => pr > quietLimited) && (
              <div className="faint small">Your aging stages at {[...new Set(agingPriorities.filter((pr) => pr > quietLimited))].map((pr) => PRIORITIES[pr]).join(" and ")} priority aren't affected by these hours. Use the aging-policy window below, or lower their priority on the Rules step.</div>
            )}
          </>
        )}
      </Panel>

      {draft.aging.enabled && (
        <Panel
          title="Aging-policy window"
          description="Only start the aging policy's jobs during these hours, whatever their priority."
          right={<Toggle checked={!!draft.policyWindow} onChange={(v) => update((d) => ({ ...d, policyWindow: v ? OVERNIGHT : null }))} label={draft.policyWindow ? "On" : "Off"} />}
        >
          {draft.policyWindow && (
            <Field {...issueProps(issues, "policy_window", showErrors)}>
              <WindowEditor value={draft.policyWindow} label="Aging-policy window" onChange={(w) => update((d) => ({ ...d, policyWindow: w }))} />
            </Field>
          )}
        </Panel>
      )}

      <Panel title="Node limits" description="How many jobs each machine runs at once, and when it should hold back.">
        {nodes.length === 0 ? (
          <div className="faint small">No nodes are registered yet. Their limits can be set on each node's page.</div>
        ) : (
          nodes.map((n) => <NodeLimits key={n.id} node={n} value={nodeDraft(draft, n)} onChange={(patch) => setNode(n, patch)} issues={issues.filter((i) => i.key === String(n.id))} showErrors={showErrors} />)
        )}
      </Panel>
    </StepFrame>
  );
}

function NodeLimits({ node, value, onChange, issues, showErrors }: { node: Node; value: NodeDraft; onChange: (p: Partial<NodeDraft>) => void; issues: Issue[]; showErrors: boolean }) {
  const rec = node.capabilities?.recommended_concurrency;
  const gpus = node.metrics?.gpus ?? [];
  const noGpu = node.capabilities ? node.capabilities.gpus.length === 0 : false;
  const gpuUnreported = gpus.length > 0 && gpus.every((g) => g.utilization == null);
  return (
    <div className="limit-row">
      <div className="node-title">
        <span className={`status-dot ${node.status}`} /> {node.name}
      </div>
      <div className="limit-grid">
        <Field label="Jobs at the same time" help={rec ? `Suggested for this hardware: ${rec}` : "1–2 suits most GPUs"} {...issueProps(issues, "concurrency", showErrors)}>
          <NumberInput value={value.max_concurrency} onChange={(v) => onChange({ max_concurrency: v })} min={1} max={16} allowEmpty />
        </Field>
        <Field label="Don't start if GPU above" help={noGpu ? "No GPU visible on this node" : gpuUnreported ? "This node doesn't report GPU load, so the limit has no effect" : "Only checked while idle"} {...issueProps(issues, "gpu", showErrors)}>
          <NumberInput value={value.max_gpu_util} onChange={(v) => onChange({ max_gpu_util: v })} min={1} max={100} suffix="%" placeholder="off" allowEmpty />
        </Field>
        <Field label="Don't start if CPU above" help="Only checked while idle" {...issueProps(issues, "cpu", showErrors)}>
          <NumberInput value={value.max_cpu_util} onChange={(v) => onChange({ max_cpu_util: v })} min={1} max={100} suffix="%" placeholder="off" allowEmpty />
        </Field>
      </div>
      <Toggle
        checked={value.reserve_slot_for_normal}
        disabled={(value.max_concurrency ?? 1) < 2}
        onChange={(v) => onChange({ reserve_slot_for_normal: v })}
        label="Keep one slot free for Normal or higher priority jobs (needs 2+ slots)"
      />
      <Toggle checked={!!value.window} onChange={(v) => onChange({ window: v ? OVERNIGHT : null })} label="Only work during certain hours" />
      {value.window && (
        <Field {...issueProps(issues, "window", showErrors)}>
          <WindowEditor value={value.window} label={`${node.name} working hours`} onChange={(w) => onChange({ window: w })} />
        </Field>
      )}
    </div>
  );
}

function WeekGrid({ draft, quiet, nodes }: { draft: SetupDraft; quiet: QuietHours | null; nodes: Node[] }) {
  const classes = useMemo(() => jobClasses(draft), [draft]);
  const [sel, setSel] = useState<string>(classes[0].key);
  const cls = classes.find((c) => c.key === sel) ?? classes[0];
  const nodeWindows = nodes.filter((n) => n.enabled).map((n) => nodeDraft(draft, n).window);

  const cells = useMemo(() => {
    const out: { day: number; hour: number; frac: number; reason: Block | null }[] = [];
    for (let day = 0; day < 7; day++) {
      for (let hour = 0; hour < 24; hour++) {
        let ok = 0;
        const reasons: Partial<Record<Block, number>> = {};
        for (let m = 0; m < 60; m += SAMPLE_MINUTES) {
          const b = blockAt(cls, day, hour * 60 + m, quiet, nodeWindows);
          if (b) reasons[b] = (reasons[b] ?? 0) + 1;
          else ok++;
        }
        const reason = (Object.entries(reasons).sort((a, b) => b[1] - a[1])[0]?.[0] as Block | undefined) ?? null;
        out.push({ day, hour, frac: ok / (60 / SAMPLE_MINUTES), reason });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cls, quiet, JSON.stringify(nodeWindows)]);

  const hours = cells.reduce((sum, c) => sum + c.frac, 0);
  const summary = nodeWindows.length === 0 ? "No enabled nodes, so nothing can start." : `${cls.label} can start during ${Math.round(hours)} of 168 hours a week.`;

  return (
    <div className="stack gap-3">
      <div className="row wrap">
        <span className="small muted">Show</span>
        <Segmented value={cls.key} onChange={setSel} options={classes.map((c) => ({ value: c.key, label: c.label }))} />
      </div>
      <div className="week-grid" role="img" aria-label={summary}>
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} className="hr">
            {h % 3 === 0 ? String(h).padStart(2, "0") : ""}
          </span>
        ))}
        {DAY_NAMES.map((name, day) => (
          <Row key={name} name={name} cells={cells.filter((c) => c.day === day)} />
        ))}
      </div>
      <div className="row wrap small gap-4">
        <span className="legend">
          <i className="sw on" /> Can start
        </span>
        <span className="legend">
          <i className="sw" /> Held back
        </span>
        <span className="faint">{summary}</span>
      </div>
    </div>
  );
}

function Row({ name, cells }: { name: string; cells: { day: number; hour: number; frac: number; reason: Block | null }[] }) {
  return (
    <>
      <span className="day">{name}</span>
      {cells.map((c) => {
        const time = `${name} ${String(c.hour).padStart(2, "0")}:00–${String(c.hour + 1).padStart(2, "0")}:00`;
        const state = c.frac === 1 ? "can start" : c.frac === 0 ? `held back: ${BLOCK_TEXT[c.reason!]}` : `can start ${Math.round(c.frac * 60)} of 60 min (${BLOCK_TEXT[c.reason!]})`;
        return <span key={c.hour} className="cell" title={`${time} · ${state}`} style={c.frac > 0 ? { background: `color-mix(in srgb, var(--accent) ${Math.round(c.frac * GRID_FILL_PCT)}%, var(--bg-sunken))` } : undefined} />;
      })}
    </>
  );
}

function ScheduleGuide() {
  return (
    <>
      <GuideSection title="Three kinds of window">
        <dl className="kv narrow-wide">
          <dt>Background work hours</dt>
          <dd>Background (or Low + Background) automatic jobs, on every node.</dd>
          <dt>Aging-policy window</dt>
          <dd>Jobs the aging policy creates, at any priority.</dd>
          <dt>Node working hours</dt>
          <dd>Everything on that node.</dd>
        </dl>
        <div>Windows may wrap past midnight (23:00–07:00). New files are only found by scans, so each library's scan interval is part of the schedule too.</div>
      </GuideSection>
      <GuideSection title="Utilization limits">
        <div>“Don't start if GPU/CPU above” keeps a node from starting work while the machine is busy with something else, such as gaming or editing. They're checked only while the node runs no FrameForge jobs, so FrameForge's own load never blocks it.</div>
      </GuideSection>
      <GuideSection title="Priorities">
        <div>Priorities are strict tiers: a Normal job never waits behind a Low one. Jobs you queue by hand default to High.</div>
      </GuideSection>
    </>
  );
}
