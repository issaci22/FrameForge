import type { FormEvent } from "react";
import { ArrowRight, Check, Circle, KeyRound, PencilLine, ShieldCheck, X } from "lucide-react";
import type { SystemInfo, User } from "../../api/types";
import { Callout, Field } from "../../components/ui";
import type { AdminForm } from "./hooks";
import { PASSWORD_LENGTH, STEPS, USERNAME_LENGTH, type StepId } from "./model";
import { GuideSection, Kv, PasswordInput, STEP_ICONS, StepFrame } from "./parts";

const PLAN: Record<Exclude<StepId, "welcome">, string> = {
  admin: "Your account. FrameForge ships without a default login.",
  storage: "The folders to watch, and where converted files go.",
  transcoding: "The codec and quality older footage is converted to.",
  rules: "How long footage stays untouched, and what happens after.",
  nodes: "The machines that encode, and what each one verified.",
  schedule: "When work may start, and how hard each machine works.",
  review: "Check everything, then apply it in one step.",
};

const ROLE_LABEL: Record<string, string> = { all: "Server + built-in node", server: "Server only" };

export function WelcomeStep({ authed, user, system, onBegin, onLater }: { authed: boolean; user: User | null; system?: SystemInfo; onBegin: () => void; onLater?: () => void }) {
  return (
    <div className="hero">
      <div className="hero-mark" aria-hidden>
        <span />
        <span />
        <span />
      </div>
      <h1 id="setup-step-title" tabIndex={-1} className="hero-title">
        Welcome to FrameForge
      </h1>
      <p className="hero-lede">
        Let's get this server ready. FrameForge leaves your recent footage untouched and, once it's old enough, converts it to a smaller format on the hardware you have. An original is only replaced after its new file has been verified.
      </p>

      <section className="hero-plan" aria-labelledby="setup-plan-title">
        <h2 id="setup-plan-title">What you'll set up</h2>
        <ol>
          {STEPS.filter((s) => s.id !== "welcome").map((s) => {
            const Icon = STEP_ICONS[s.id];
            return (
              <li key={s.id}>
                <span className="plan-icon" aria-hidden>
                  <Icon size={16} />
                </span>
                <span className="plan-text">
                  <b>{s.label}</b>
                  <span>{PLAN[s.id as Exclude<StepId, "welcome">]}</span>
                </span>
              </li>
            );
          })}
        </ol>
      </section>

      <ul className="hero-notes">
        <li>
          <ShieldCheck size={16} aria-hidden />
          <span>
            <b>Setup only reads your folders.</b> Files change only when a job runs, and never before the new file passes its checks.
          </span>
        </li>
        <li>
          <PencilLine size={16} aria-hidden />
          <span>
            <b>Everything after the account is a draft.</b> It's applied together at the end, and you can go back to any step until then.
          </span>
        </li>
      </ul>

      <div className="hero-cta">
        <button type="button" className="btn primary lg" onClick={onBegin}>
          {authed ? "Continue setup" : "Begin setup"} <ArrowRight size={16} />
        </button>
        {onLater && (
          <button type="button" className="btn ghost lg" onClick={onLater} title="Your draft stays in this browser tab. Open /setup to continue.">
            Finish later
          </button>
        )}
      </div>
      <p className="hero-foot">
        {authed && user ? (
          <>
            Signed in as <b>{user.username}</b>. Your draft is kept in this browser tab.
          </>
        ) : (
          "Nothing is saved until you create the account."
        )}
        {system && (
          <span className="hero-sys">
            FrameForge {system.version} · {ROLE_LABEL[system.role] ?? system.role} · {system.database === "sqlite" ? "SQLite" : "PostgreSQL"}
          </span>
        )}
      </p>
    </div>
  );
}

function Req({ met, touched, children }: { met: boolean; touched: boolean; children: string }) {
  const state = met ? "met" : touched ? "miss" : "";
  return (
    <li className={state}>
      {met ? <Check size={13} /> : touched ? <X size={13} /> : <Circle size={11} />}
      {children}
    </li>
  );
}

const ACCESS_GUIDE = (
  <>
    <GuideSection title="How access works">
      <Kv
        rows={[
          ["Sign-in", "Local account. The password is hashed with argon2id and never stored."],
          ["Sessions", "HttpOnly cookie, valid 30 days unless FF_SESSION_DAYS is set. The server keeps only a SHA-256 hash of the token."],
        ]}
      />
    </GuideSection>
    <GuideSection title="Protection">
      <Kv
        rows={[
          ["Sign-in limit", "10 failed sign-ins from one address within 5 minutes pause sign-in for that address."],
          ["Other users", "Not available in this version: one administrator account."],
        ]}
      />
    </GuideSection>
  </>
);

/** The account step: the form, the moment right after it's created, and the "already exists" view on revisit. */
export function AdminStep({ form, user, created }: { form: AdminForm; user: User | null; created: User | null }) {
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void form.submit();
  };
  const t = form.touched;
  // Don't flag a mismatch while the confirmation is still being typed.
  const mismatch = form.confirm.length > 0 && !form.checks.match && (t || form.confirm.length >= form.password.length);
  const account = created ?? user;

  if (account) {
    return (
      <StepFrame step="admin" narrow title="Administrator account" lede="The account exists and this browser is signed in with it." guide={ACCESS_GUIDE}>
        <section className={`panel account-done ${created ? "fresh" : ""}`} role={created ? "status" : undefined}>
          <div className="account-head">
            <span className="done-badge" aria-hidden>
              <Check size={20} strokeWidth={2.5} />
            </span>
            <div className="min0">
              <h2>{created ? "Account created" : "Primary administrator"}</h2>
              <div className="muted small">
                Signed in as <span className="mono">{account.username}</span>
              </div>
            </div>
          </div>
          {created ? (
            <div className="account-next">
              <div className="account-next-bar" aria-hidden>
                <i />
              </div>
              <span className="faint small">Continuing to Storage…</span>
            </div>
          ) : (
            <div className="panel-body stack gap-3">
              <Kv
                rows={[
                  ["Role", "Administrator: full control of this FrameForge instance"],
                  ["Session", "Signed in on this browser"],
                ]}
              />
              <div className="faint small">Setup can't create this account a second time. The server refuses once any account exists.</div>
            </div>
          )}
        </section>
      </StepFrame>
    );
  }

  return (
    <StepFrame
      step="admin"
      narrow
      title="Create your administrator account"
      lede="This account controls the whole server: libraries, rules, nodes and every file operation. It lives only on this server."
      guide={ACCESS_GUIDE}
    >
      <form id="setup-admin-form" className="panel admin-card" onSubmit={onSubmit} noValidate>
        <div className="panel-body stack">
          <Field label="Username" htmlFor="setup-username" help={`${USERNAME_LENGTH.min}–${USERNAME_LENGTH.max} characters: letters, digits, _ . -`} error={t && !(form.checks.userLength && form.checks.userChars) ? "Use 2–64 letters, digits, underscores, dots or hyphens." : null}>
            <input
              id="setup-username"
              className="input mono"
              value={form.username}
              onChange={(e) => form.setUsername(e.target.value.trim())}
              autoComplete="username"
              autoFocus
              spellCheck={false}
              aria-invalid={(t && !(form.checks.userLength && form.checks.userChars)) || undefined}
            />
          </Field>
          <Field label="Password" htmlFor="setup-password">
            <PasswordInput id="setup-password" value={form.password} onChange={form.setPassword} autoComplete="new-password" invalid={t && !form.checks.passwordLength} describedBy="setup-reqs" />
          </Field>
          <Field label="Confirm password" htmlFor="setup-confirm" error={mismatch ? "Passwords don't match." : null}>
            <PasswordInput id="setup-confirm" value={form.confirm} onChange={form.setConfirm} autoComplete="new-password" invalid={mismatch} />
          </Field>
          <ul className="req-list" id="setup-reqs" aria-label="Requirements">
            <Req met={form.checks.userLength && form.checks.userChars} touched={t}>
              Valid username
            </Req>
            <Req met={form.checks.passwordLength} touched={t}>{`At least ${PASSWORD_LENGTH.min} characters`}</Req>
            <Req met={form.checks.match} touched={t}>
              Passwords match
            </Req>
          </ul>
          {form.error && (
            <div className="appear">
              <Callout kind="err" title="The account wasn't created">
                {form.error}
              </Callout>
            </div>
          )}
        </div>
      </form>
      <p className="admin-note">
        <KeyRound size={15} aria-hidden />
        <span>
          There's no password reset, so keep it in a password manager and back up the <span className="mono">/config</span> volume. You can change it later under Settings → Account.
        </span>
      </p>
    </StepFrame>
  );
}
