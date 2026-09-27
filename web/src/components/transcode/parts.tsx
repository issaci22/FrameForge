import type { ReactNode } from "react";
import { AlertTriangle, Info, XCircle } from "lucide-react";
import type { CompatIssue, ProfileOptions, ProfileSpec } from "../../api/types";

export type SpecPatch = Partial<ProfileSpec>;
export type OnSpec = (patch: SpecPatch) => void;

/** One titled block of the profile editor. */
export function TxSection({ title, hint, right, children }: { title: string; hint?: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="tx-section">
      <div className="tx-head">
        <h3>{title}</h3>
        {hint && <span className="faint small">{hint}</span>}
        {right && <span className="right">{right}</span>}
      </div>
      <div className="tx-body">{children}</div>
    </section>
  );
}

const ICONS = { error: XCircle, warn: AlertTriangle, info: Info };

/** Compatibility advice for some spec fields, each with its one-click fix when there is one. */
export function CompatIssues({ issues, fields, onApply }: { issues: CompatIssue[] | undefined; fields?: string[]; onApply: OnSpec }) {
  const shown = (issues ?? []).filter((i) => !fields || fields.includes(i.field));
  if (!shown.length) return null;
  return (
    <ul className="tx-issues">
      {shown.map((i) => {
        const Icon = ICONS[i.level];
        return (
          <li key={`${i.field}-${i.message}`} className={`tx-issue ${i.level}`}>
            <Icon size={13} aria-hidden />
            <span>{i.message}</span>
            {i.suggestion && (
              <button type="button" className="btn sm ghost" onClick={() => onApply(i.suggestion!)}>
                {i.suggestion_label ?? "Fix"}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export type HwState = "gpu" | "cpu" | "none" | "unknown";

/** Whether online nodes verified a GPU encoder, only a CPU encoder, or nothing for a codec. */
export function hardwareFor(options: ProfileOptions | undefined, codec: string): { state: HwState; detail: string } {
  if (!options || options.online_nodes === 0) return { state: "unknown", detail: "No node is online, so hardware can't be checked right now." };
  if (codec === "copy") return { state: "cpu", detail: "Copying needs no encoder." };
  const byBackend = options.hardware[codec] ?? {};
  const gpu = Object.entries(byBackend).filter(([b]) => b !== "cpu");
  if (gpu.length) {
    const names = gpu.map(([b, nodes]) => `${options.backend_labels[b] ?? b} on ${nodes.join(", ")}`);
    return { state: "gpu", detail: `Verified: ${names.join("; ")}` };
  }
  if (byBackend.cpu?.length) return { state: "cpu", detail: `Only the CPU encoder (on ${byBackend.cpu.join(", ")}). Works, but slowly for H.265 and AV1.` };
  return { state: "none", detail: "No online node can encode this right now." };
}

const HW_TEXT: Record<HwState, string> = { gpu: "GPU", cpu: "CPU", none: "None online", unknown: "Unchecked" };

export function HwBadge({ state, title }: { state: HwState; title: string }) {
  return (
    <span className={`tx-hw ${state}`} title={title}>
      <span className="dot" aria-hidden />
      {HW_TEXT[state]}
    </span>
  );
}
