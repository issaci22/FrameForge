import { Check, Lock } from "lucide-react";
import type { StepId } from "./model";

export type StepState = "current" | "done" | "error" | "locked" | "todo";

export interface StepItem {
  id: StepId;
  label: string;
  state: StepState;
  errors: number;
  /** A dot on the circle: problems on the current step (once shown) or notes worth a look. */
  flag: "err" | "warn" | null;
  summary: string;
}

const STATE_TEXT: Record<StepState, string> = {
  current: "current step",
  done: "done",
  error: "needs attention",
  locked: "available after the account is created",
  todo: "not visited yet",
};

/**
 * Horizontal progress through the wizard. The line fills up to the current step; each circle shows that
 * step's own state. Every reachable step is a button, so earlier answers are one click away.
 */
export function Stepper({ items, current, disabled, onGo }: { items: StepItem[]; current: number; disabled?: boolean; onGo: (id: StepId) => void }) {
  return (
    <nav className="stepper" aria-label="Setup progress">
      <ol>
        {items.map((s, i) => {
          const locked = s.state === "locked";
          const detail = s.state === "error" ? `${s.errors} problem${s.errors === 1 ? "" : "s"} to fix` : STATE_TEXT[s.state];
          return (
            <li key={s.id} className={`st ${s.state} ${i <= current ? "reached" : ""}`}>
              <button
                type="button"
                aria-current={s.state === "current" ? "step" : undefined}
                aria-label={`${s.label}, step ${i + 1} of ${items.length}: ${detail}${s.summary && !locked ? `. ${s.summary}` : ""}`}
                title={locked ? "Create the administrator account first" : s.summary || undefined}
                disabled={locked || disabled}
                onClick={() => onGo(s.id)}
              >
                <span className="st-dot" aria-hidden>
                  {s.state === "done" ? <Check size={14} strokeWidth={2.5} /> : locked ? <Lock size={12} /> : s.state === "error" ? s.errors : i + 1}
                  {s.flag && <span className={`st-flag ${s.flag}`} />}
                </span>
                <span className="st-label" aria-hidden>
                  {s.label}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
