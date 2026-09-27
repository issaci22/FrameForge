import { useId, useState, type ReactNode } from "react";
import { AlertTriangle, BookOpen, CalendarClock, ChevronDown, Cpu, DoorOpen, Eye, EyeOff, Film, HardDrive, Hourglass, KeyRound, ListChecks, type LucideIcon } from "lucide-react";
import type { TimeWindow } from "../../api/types";
import { Section } from "../../components/ui";
import { ALL_DAYS, DAY_NAMES, fieldIssue, stepIndex, STEPS, type Issue, type ServerData, type SetupDraft, type StepId } from "./model";

export interface StepProps {
  draft: SetupDraft;
  update: (fn: (d: SetupDraft) => SetupDraft) => void;
  server: ServerData;
  issues: Issue[];
  showErrors: boolean;
}

export const STEP_ICONS: Record<StepId, LucideIcon> = {
  welcome: DoorOpen,
  admin: KeyRound,
  storage: HardDrive,
  transcoding: Film,
  rules: Hourglass,
  nodes: Cpu,
  schedule: CalendarClock,
  review: ListChecks,
};

const GUIDE_KEY = "ff-setup-guide";

function guideOpenByDefault(): boolean {
  try {
    return sessionStorage.getItem(GUIDE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Honors the OS "reduce motion" setting for scripted scrolling (CSS handles the rest). */
export function scrollBehavior(): ScrollBehavior {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

/**
 * A step's header (icon, position, title, why it matters) and its optional "How this works" guide.
 * The guide replaces the old side column: the flow stays one centered column, and the background
 * material is one click away. Its open state carries over to the next step.
 */
export function StepFrame({ step, title, lede, guide, narrow, children }: { step: StepId; title: string; lede?: ReactNode; guide?: ReactNode; narrow?: boolean; children: ReactNode }) {
  const Icon = STEP_ICONS[step];
  const guideId = useId();
  const [open, setOpen] = useState(guideOpenByDefault);
  const toggle = () =>
    setOpen((o) => {
      try {
        sessionStorage.setItem(GUIDE_KEY, o ? "0" : "1");
      } catch {
        /* storage unavailable: the choice lasts for this step */
      }
      return !o;
    });

  return (
    <div className={`step ${narrow ? "narrow" : ""}`}>
      <header className="step-head">
        <span className="step-icon" aria-hidden>
          <Icon size={20} />
        </span>
        <div className="step-titles">
          <div className="step-eyebrow">
            Step {stepIndex(step) + 1} of {STEPS.length}
          </div>
          <h1 id="setup-step-title" tabIndex={-1}>
            {title}
          </h1>
          {lede && <p className="step-lede">{lede}</p>}
          {guide && (
            <button type="button" className="guide-toggle" aria-expanded={open} aria-controls={guideId} onClick={toggle}>
              <BookOpen size={14} /> How this works <ChevronDown size={14} className="chev" />
            </button>
          )}
        </div>
      </header>
      {guide && (
        <div id={guideId} className={`guide-wrap ${open ? "open" : ""}`} inert={!open}>
          <div className="guide-clip">
            <div className="setup-guide">{guide}</div>
          </div>
        </div>
      )}
      <div className="setup-work">{children}</div>
    </div>
  );
}

export function GuideSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="guide-sec">
      <h3>{title}</h3>
      <div className="body">{children}</div>
    </section>
  );
}

/** A titled surface inside a step: the shared `Section`, with its body stacked. */
export function Panel({ title, description, right, children, tight }: { title: ReactNode; description?: ReactNode; right?: ReactNode; children: ReactNode; tight?: boolean }) {
  return (
    <Section title={title} description={description} actions={right} flush={tight}>
      {tight ? children : <div className="stack">{children}</div>}
    </Section>
  );
}

export type Tone = "ok" | "warn" | "err" | "info" | "accent" | "neutral" | "outline";

export function StatusChip({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`chip caps ${tone === "neutral" ? "" : tone}`} title={title}>
      {children}
    </span>
  );
}

/** Field-level error/warning, as props for <Field>. Errors show once the step was attempted; warnings always. */
export function issueProps(issues: Issue[], field: string, showErrors: boolean, key?: string): { error?: string | null; help?: ReactNode } {
  const i = fieldIssue(issues, field, key);
  if (!i) return {};
  if (i.level === "error") return showErrors ? { error: i.text } : {};
  return { help: <WarnText>{i.text}</WarnText> };
}

/** The same issue as a standalone line, for sections that aren't a single <Field>. */
export function IssueNote({ issues, field, showErrors, issueKey }: { issues: Issue[]; field: string; showErrors: boolean; issueKey?: string }) {
  const { error, help } = issueProps(issues, field, showErrors, issueKey);
  if (error) return <div className="tx-err small">{error}</div>;
  return help ? <div className="small">{help}</div> : null;
}

export function WarnText({ children }: { children: ReactNode }) {
  return (
    <span className="warn-text">
      <AlertTriangle size={12} /> {children}
    </span>
  );
}

export function IssueList({ issues, showErrors }: { issues: Issue[]; showErrors: boolean }) {
  const shown = issues.filter((i) => i.level === "warn" || showErrors);
  if (!shown.length) return null;
  return (
    <ul className="issue-list">
      {shown.map((i, n) => (
        <li key={n} className={i.level}>
          {i.text}
        </li>
      ))}
    </ul>
  );
}

export function PasswordInput({ value, onChange, autoComplete, id, invalid, onBlur, describedBy }: { value: string; onChange: (v: string) => void; autoComplete: string; id: string; invalid?: boolean; onBlur?: () => void; describedBy?: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="pw-field">
      <input
        id={id}
        className="input"
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        autoComplete={autoComplete}
        spellCheck={false}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
      />
      <button type="button" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"} aria-pressed={show} title={show ? "Hide password" : "Show password"}>
        {show ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  );
}

export function DayPicker({ value, onChange, disabled }: { value: number[]; onChange: (days: number[]) => void; disabled?: boolean }) {
  return (
    <div className="day-chips" role="group" aria-label="Days">
      {DAY_NAMES.map((name, d) => {
        const on = value.includes(d);
        return (
          <button key={d} type="button" aria-pressed={on} disabled={disabled} onClick={() => onChange(on ? value.filter((x) => x !== d) : [...value, d].sort())}>
            {name}
          </button>
        );
      })}
    </div>
  );
}

export function WindowEditor({ value, onChange, disabled, label }: { value: TimeWindow; onChange: (w: TimeWindow) => void; disabled?: boolean; label: string }) {
  const days = value.days ?? ALL_DAYS;
  const wraps = value.start > value.end;
  return (
    <div className="window-editor">
      <div className="row wrap">
        <input className="input mono w-time" type="time" aria-label={`${label} start`} disabled={disabled} value={value.start} onChange={(e) => onChange({ ...value, start: e.target.value })} />
        <span className="faint small">to</span>
        <input className="input mono w-time" type="time" aria-label={`${label} end`} disabled={disabled} value={value.end} onChange={(e) => onChange({ ...value, end: e.target.value })} />
        <DayPicker value={days} disabled={disabled} onChange={(d) => onChange({ ...value, days: d })} />
      </div>
      {!disabled && (
        <div className="faint small">
          {value.start === value.end ? "Equal start and end times mean all day." : wraps ? "Runs past midnight. The day it starts on decides whether it applies." : null}
        </div>
      )}
    </div>
  );
}

export function Kv({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v], i) => (
        <KvRow key={i} k={k} v={v} />
      ))}
    </dl>
  );
}

function KvRow({ k, v }: { k: ReactNode; v: ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}
