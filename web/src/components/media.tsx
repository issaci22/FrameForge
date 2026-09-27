import { ArrowRight } from "lucide-react";
import type { JobState, MetricPoint } from "../api/types";
import { codec as codecLabel, priorityName } from "../lib/format";

export function CodecChip({ codec }: { codec: string | null | undefined }) {
  const cls = codec === "h264" || codec === "hevc" || codec === "av1" ? codec : "other";
  return <span className={`codec ${cls}`}>{codecLabel(codec)}</span>;
}

export function Transform({ from, to }: { from: string | null | undefined; to: string | null | undefined }) {
  return (
    <span className="transform">
      <CodecChip codec={from} />
      <ArrowRight size={12} />
      <CodecChip codec={to} />
    </span>
  );
}

const STATE_META: Record<JobState, { label: string; cls: string }> = {
  queued: { label: "Queued", cls: "" },
  assigned: { label: "Starting", cls: "accent" },
  preparing: { label: "Preparing", cls: "accent" },
  transcoding: { label: "Transcoding", cls: "accent" },
  validating: { label: "Validating", cls: "info" },
  finalizing: { label: "Finalizing", cls: "info" },
  completed: { label: "Completed", cls: "ok" },
  failed: { label: "Failed", cls: "err" },
  cancelled: { label: "Cancelled", cls: "" },
};

export function StateChip({ state }: { state: JobState }) {
  const m = STATE_META[state] ?? { label: state, cls: "" };
  return (
    <span className={`chip ${m.cls}`}>
      <span className="dot" />
      {m.label}
    </span>
  );
}

export function PriorityChip({ priority }: { priority: number }) {
  const cls = priority >= 4 ? "err" : priority === 3 ? "warn" : priority <= 0 ? "outline" : "";
  return <span className={`chip ${cls}`}>{priorityName(priority)}</span>;
}

export function Timeline({ value, state, thin }: { value: number; state?: "run" | "ok" | "err" | "wait"; thin?: boolean }) {
  const v = Math.max(0, Math.min(100, value));
  const cls = state === "ok" ? "ok" : state === "err" ? "err" : state === "wait" ? "indeterminate" : "";
  return (
    <div className={`timeline ${cls} ${thin ? "thin" : ""}`} role="progressbar" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100}>
      <div className="fill" style={{ width: `${v}%` }} />
    </div>
  );
}

/** Load meter: a single bar that turns amber above 70% and red above 90%. */
export function Meter({ name, value, label, max = 100 }: { name: string; value: number | null | undefined; label?: string; max?: number }) {
  const ratio = value == null ? 0 : Math.max(0, Math.min(1, value / max));
  const tone = ratio >= 0.9 ? "max" : ratio >= 0.7 ? "hot" : "";
  return (
    <div className="meter">
      <span className="name">{name}</span>
      <div className="bar" role="meter" aria-label={name} aria-valuenow={value == null ? undefined : Math.round(ratio * 100)} aria-valuemin={0} aria-valuemax={100}>
        <i className={tone} style={{ width: `${ratio * 100}%` }} />
      </div>
      <span className="val">{value == null ? "not reported" : label ?? `${Math.round(value)}%`}</span>
    </div>
  );
}

export function Sparkline({ points, keyName, height = 44, color = "var(--accent)", max = 100 }: { points: MetricPoint[]; keyName: keyof MetricPoint; height?: number; color?: string; max?: number }) {
  const width = 300;
  const vals = points.map((p) => p[keyName] as number | null);
  if (vals.every((v) => v == null)) {
    return (
      <div className="faint small spark-none" style={{ height }}>
        Not reported by this node
      </div>
    );
  }
  const n = Math.max(2, vals.length);
  const path = vals
    .map((v, i) => {
      const x = (i / (n - 1)) * width;
      const y = height - ((v ?? 0) / max) * (height - 2) - 1;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ height }}>
      {[25, 50, 75].map((g) => (
        <line key={g} x1={0} x2={width} y1={height - (g / 100) * height} y2={height - (g / 100) * height} stroke="var(--line-soft)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
      ))}
      <path d={`${path} L${width},${height} L0,${height} Z`} fill={color} opacity={0.12} />
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
