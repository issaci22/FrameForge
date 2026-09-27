import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, ArrowRight, Check } from "lucide-react";
import { keys, useAuth, useLibraries, useNodes, useProfiles, useRuleFields, useSettings, useSystem } from "../../api/hooks";
import type { User } from "../../api/types";
import { Logo, useToast } from "../../components/ui";
import { AdminStep, WelcomeStep } from "./AccessSteps";
import { Finished, type FinishedInfo } from "./Finished";
import { useAdminForm, useCommit, useFolderChecks } from "./hooks";
import {
  clearDraft,
  computeIssues,
  draftPaths,
  effectiveQuietHours,
  errorsIn,
  initialDraft,
  loadDraft,
  saveDraft,
  stepIndex,
  STEPS,
  withProfileDefaults,
  type ServerData,
  type SetupDraft,
  type StepId,
} from "./model";
import { NodesStep } from "./NodesStep";
import { scrollBehavior } from "./parts";
import { ReviewStep } from "./ReviewStep";
import { RulesStep } from "./RulesStep";
import { ScheduleStep } from "./ScheduleStep";
import { Stepper, type StepItem } from "./Stepper";
import { StorageStep } from "./StorageStep";
import { TranscodingStep } from "./TranscodingStep";

const ADMIN = stepIndex("admin");
/** How long "Account created" stays on screen before the wizard moves on to Storage. */
const ACCOUNT_PAUSE_MS = 1400;
const FIRST_ERROR = ".setup-work .error-text, .setup-work .issue-list .error, .setup-work .path-status.err";

export function SetupPage() {
  const { data: auth } = useAuth();
  const authed = !!auth?.authenticated;
  const user = auth?.user ?? null;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();

  const finished = useRef(false);
  const [draft, setDraft] = useState<SetupDraft>(() => loadDraft() ?? initialDraft());
  const [attempted, setAttempted] = useState<StepId[]>([]);
  const [created, setCreated] = useState<User | null>(null);
  const [finishedInfo, setFinishedInfo] = useState<FinishedInfo | null>(null);
  // Saved synchronously so a remount (auth state change) or a reload resumes where the admin left off.
  const update = useCallback(
    (fn: (d: SetupDraft) => SetupDraft) =>
      setDraft((d) => {
        const next = fn(d);
        if (!finished.current) saveDraft(next);
        return next;
      }),
    [],
  );

  // Before the account exists nothing past the admin step is reachable (the API would refuse it anyway).
  const step: StepId = !authed && stepIndex(draft.step) > ADMIN ? "welcome" : draft.step;
  const idx = stepIndex(step);

  // Which way the flow moved, so the next step slides in from the matching side.
  const travel = useRef({ idx, dir: "fwd" as "fwd" | "back" });
  if (travel.current.idx !== idx) travel.current = { idx, dir: idx > travel.current.idx ? "fwd" : "back" };

  const profiles = useProfiles(authed).data;
  const nodes = useNodes(authed).data;
  const libraries = useLibraries(authed).data;
  const settings = useSettings(authed).data;
  const fields = useRuleFields(authed).data?.fields;
  const system = useSystem(authed).data;
  const server = useMemo<ServerData>(() => ({ profiles, nodes, libraries, settings, fields }), [profiles, nodes, libraries, settings, fields]);

  useEffect(() => {
    if (profiles?.length && draft.archiveProfileId == null) update((d) => (d.archiveProfileId == null ? withProfileDefaults(d, profiles) : d));
  }, [profiles, draft.archiveProfileId, update]);

  const folders = useFolderChecks(authed ? draftPaths(draft) : [], authed);
  const issues = computeIssues(draft, server, folders, authed);
  const seen = (id: StepId) => draft.visited.includes(id) || attempted.includes(id);
  const showErrors = step === "review" || seen(step);

  const go = (to: StepId) => update((d) => ({ ...d, step: to, visited: d.visited.includes(step) ? d.visited : [...d.visited, step] }));

  // The account exists as soon as the API answers; show that for a moment, then move on signed in.
  const admin = useAdminForm((u) => {
    setCreated(u);
    window.setTimeout(() => {
      setCreated(null);
      update((d) => ({ ...d, step: "storage", visited: [...new Set([...d.visited, "welcome" as StepId, "admin" as StepId])] }));
      qc.setQueryData(keys.auth, { setup_required: false, authenticated: true, user: u });
      toast(`Administrator “${u.username}” created. You're signed in.`);
    }, ACCOUNT_PAUSE_MS);
  });

  const commit = useCommit(draft, server, update, (warnings) => {
    finished.current = true;
    clearDraft();
    for (const key of [keys.libraries, keys.rules, keys.profiles, keys.nodes, keys.settings, ["aging"], ["stats"], ["jobs"], ["files"]]) qc.invalidateQueries({ queryKey: key });
    setFinishedInfo({ libraries: draft.libraries.map((l) => l.name.trim()), automation: draft.automation, warnings });
  });

  // New step: back to the top, and focus its heading for keyboard and screen-reader users. On the first render
  // (a reload, or the remount when the account is created) only if nothing else has focus.
  const scrollRef = useRef<HTMLElement>(null);
  const firstRender = useRef(true);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
    const first = firstRender.current;
    firstRender.current = false;
    if (step === "admin" && !authed) return; // the username field takes focus
    if (first && document.activeElement && document.activeElement !== document.body) return;
    document.getElementById("setup-step-title")?.focus({ preventScroll: true });
  }, [step, authed]);

  const stepErrors = errorsIn(issues[step]);
  const totalErrors = STEPS.reduce((n, s) => n + errorsIn(issues[s.id]).length, 0);
  const next = STEPS[idx + 1]?.id;
  const prev = STEPS[idx - 1]?.id;

  const onContinue = () => {
    if (step === "welcome") return go("admin");
    if (!next) return;
    if (stepErrors.length) {
      setAttempted((a) => (a.includes(step) ? a : [...a, step]));
      // After the errors render: bring the first one into view and draw the eye to it once.
      window.setTimeout(() => {
        const el = document.querySelector<HTMLElement>(FIRST_ERROR);
        if (!el) return;
        el.scrollIntoView({ block: "center", behavior: scrollBehavior() });
        el.classList.remove("attention");
        void el.offsetWidth;
        el.classList.add("attention");
        el.addEventListener("animationend", () => el.classList.remove("attention"), { once: true });
      }, 0);
      return;
    }
    go(next);
  };

  const onApply = () => {
    document.getElementById("setup-apply")?.scrollIntoView({ block: "start", behavior: scrollBehavior() });
    void commit.run();
  };

  if (finishedInfo) {
    return (
      <div className="setup is-hero">
        <main className="setup-scroll">
          <div className="setup-stage hero-stage">
            <Finished info={finishedInfo} onOpen={() => navigate("/", { replace: true })} />
          </div>
        </main>
      </div>
    );
  }

  const summaries = stepSummaries(draft, server, authed, user?.username);
  const items: StepItem[] = STEPS.map((s, i) => {
    const locked = !authed && i > ADMIN;
    const errs = errorsIn(issues[s.id]).length;
    const warns = issues[s.id].length - errs;
    const visited = s.id === "admin" ? authed : seen(s.id);
    const state = s.id === step ? "current" : locked ? "locked" : visited && errs ? "error" : visited ? "done" : "todo";
    const noted = warns > 0 && s.id !== "review";
    const flag = state === "current" ? (errs && showErrors ? "err" : noted ? "warn" : null) : state === "done" && noted ? "warn" : null;
    return { id: s.id, label: s.label, state, errors: errs, flag, summary: locked ? "" : summaries[s.id] };
  });

  const hero = step === "welcome";
  const narrow = step === "admin";
  const stepProps = { draft, update, server, issues: issues[step], showErrors };

  return (
    <div className={`setup ${hero ? "is-hero" : ""}`}>
      {!hero && (
        <header className="setup-top">
          <Logo />
          <Stepper items={items} current={idx} disabled={commit.running || !!created} onGo={go} />
          <div className="setup-meta">
            {user && (
              <span className="who" title={`Signed in as ${user.username}`}>
                Signed in as <b>{user.username}</b>
              </span>
            )}
            {authed && step !== "review" && (
              <button type="button" className="btn sm ghost" title="Your draft stays in this browser tab. Open /setup to continue." onClick={() => navigate("/")}>
                Finish later
              </button>
            )}
          </div>
        </header>
      )}

      <main className="setup-scroll" ref={scrollRef}>
        <div key={step} className={`setup-stage ${hero ? "hero-stage" : ""} ${travel.current.dir}`}>
          {step === "welcome" && <WelcomeStep authed={authed} user={user} system={system} onBegin={onContinue} onLater={authed ? () => navigate("/") : undefined} />}
          {step === "admin" && <AdminStep form={admin} user={authed ? user : null} created={created} />}
          {step === "storage" && <StorageStep {...stepProps} folders={folders} />}
          {step === "transcoding" && <TranscodingStep {...stepProps} />}
          {step === "rules" && <RulesStep {...stepProps} />}
          {step === "nodes" && <NodesStep {...stepProps} />}
          {step === "schedule" && <ScheduleStep {...stepProps} />}
          {step === "review" && <ReviewStep {...stepProps} all={issues} user={user} commit={commit} onEdit={go} />}
        </div>
      </main>

      {!hero && (
        <footer className={`setup-foot ${narrow ? "narrow" : ""}`}>
          <div className="setup-foot-inner">
            <button type="button" className="btn ghost" disabled={!prev || commit.running || admin.busy || !!created} onClick={() => prev && go(prev)}>
              <ArrowLeft size={14} /> Back
            </button>
            <FooterStatus step={step} authed={authed} created={!!created} failed={commit.failed} stepErrors={stepErrors.length} warns={issues[step].length - stepErrors.length} showErrors={showErrors} totalErrors={totalErrors} running={commit.running} />
            {step === "admin" && !authed ? (
              created ? (
                <button type="button" className="btn primary" disabled>
                  <Check size={14} /> Account created
                </button>
              ) : (
                <button type="submit" form="setup-admin-form" className="btn primary" disabled={admin.busy}>
                  {admin.busy ? "Creating account…" : "Create account & continue"} <ArrowRight size={14} />
                </button>
              )
            ) : step === "review" ? (
              <button type="button" className="btn primary" disabled={totalErrors > 0 || commit.running || !authed} onClick={onApply}>
                {commit.running ? `Applying… ${commit.done}/${commit.tasks.length}` : commit.failed ? "Retry & complete setup" : "Complete setup"}
                {!commit.running && <Check size={14} />}
              </button>
            ) : (
              <button type="button" className="btn primary" onClick={onContinue}>
                Continue to {STEPS[idx + 1].label} <ArrowRight size={14} />
              </button>
            )}
          </div>
        </footer>
      )}
    </div>
  );
}

function FooterStatus({ step, authed, created, failed, stepErrors, warns, showErrors, totalErrors, running }: { step: StepId; authed: boolean; created: boolean; failed: boolean; stepErrors: number; warns: number; showErrors: boolean; totalErrors: number; running: boolean }) {
  let text: string;
  let tone = "";
  if (step === "admin") [text, tone] = created ? ["Account created. Continuing to Storage…", "ok"] : [authed ? "Account created" : "Creates the account and signs you in", ""];
  else if (step === "review") {
    if (running) text = "Applying configuration…";
    else if (totalErrors) [text, tone] = [`Fix ${totalErrors} problem${totalErrors > 1 ? "s" : ""} before applying`, "err"];
    else if (failed) [text, tone] = ["Stopped at a failed step", "err"];
    else text = "Ready to apply";
  } else if (stepErrors && showErrors) [text, tone] = [`${stepErrors} problem${stepErrors > 1 ? "s" : ""} on this step`, "err"];
  else if (warns) [text, tone] = [`${warns} note${warns > 1 ? "s" : ""} on this step`, "warn"];
  else text = "Draft: applied when you complete setup";
  return (
    <div className={`status ${tone}`} role="status" aria-live="polite">
      {tone === "ok" ? <Check size={13} /> : tone && <AlertTriangle size={13} />}
      <span>{text}</span>
    </div>
  );
}

function stepSummaries(d: SetupDraft, server: ServerData, authed: boolean, username: string | undefined): Record<StepId, string> {
  const archive = server.profiles?.find((p) => p.id === d.archiveProfileId);
  const online = server.nodes?.filter((n) => n.online).length ?? 0;
  const quiet = effectiveQuietHours(d, server.settings);
  const libs = d.libraries.length;
  return {
    welcome: "Overview",
    admin: authed ? `Signed in as ${username ?? "admin"}` : "Required",
    storage: libs ? `${libs} ${libs === 1 ? "library" : "libraries"}` : "No libraries",
    transcoding: archive ? `${archive.name}${d.profileEdits[archive.id] ? " · modified" : ""}` : authed ? "Choose a profile" : "",
    rules: [d.aging.enabled ? `Aging: ${d.aging.stages.length} stage${d.aging.stages.length === 1 ? "" : "s"}` : "No aging policy", d.exceptions.length ? `${d.exceptions.length} rule${d.exceptions.length > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · "),
    nodes: server.nodes ? `${online}/${server.nodes.length} online` : "",
    schedule: quiet?.enabled ? `Background ${quiet.window.start}–${quiet.window.end}` : "No restrictions",
    review: "Apply configuration",
  };
}
