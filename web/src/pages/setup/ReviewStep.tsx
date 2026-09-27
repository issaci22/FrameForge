import type { ReactNode } from "react";
import { Check, Circle, Loader2, Pencil, XCircle } from "lucide-react";
import type { ConditionLeaf, FieldDef, User } from "../../api/types";
import { CodecChip, Timeline } from "../../components/media";
import { storageTitle, usesBackupFolder, usesOutputFolder } from "../../lib/storage";
import { Callout, Section } from "../../components/ui";
import { PRIORITIES } from "../../lib/format";
import { accelSummary } from "../Nodes";
import type { Commit } from "./hooks";
import { useProfilePreview } from "./hooks";
import { DAY_NAMES, effectivePublicUrl, effectiveQuietHours, effectiveSpec, errorsIn, nodeDraft, STEPS, type Issue, type StepId } from "./model";
import { GuideSection, StatusChip, StepFrame, type StepProps } from "./parts";

function describeWindow(w: { start: string; end: string; days?: number[] } | null | undefined): string {
  if (!w) return "any time";
  const days = !w.days || w.days.length === 0 || w.days.length === 7 ? "every day" : w.days.map((d) => DAY_NAMES[d]).join(", ");
  return `${w.start}–${w.end}, ${days}`;
}

export function describeCondition(c: ConditionLeaf, fields: FieldDef[] | undefined): string {
  const f = fields?.find((x) => x.key === c.field);
  const op = f?.operators.find((o) => o.key === c.operator)?.label ?? c.operator;
  const label = (v: unknown) => String(f?.options?.find((o) => o.value === v)?.label ?? v);
  const value = f?.kind === "bool" ? "" : Array.isArray(c.value) ? c.value.map(label).join(c.operator === "between" ? "–" : ", ") : label(c.value);
  const unit = f?.unit && f.kind === "number" && f.unit !== "p" ? ` ${f.unit}` : "";
  return `${f?.label ?? c.field} ${op} ${value}${unit}`.trim();
}

function ReviewCard({ title, step, issues, onEdit, children, empty }: { title: string; step: StepId; issues: Issue[]; onEdit: (s: StepId) => void; children: ReactNode; empty?: boolean }) {
  const errors = errorsIn(issues);
  const warns = issues.filter((i) => i.level === "warn");
  const chip =
    errors.length > 0 ? (
      <StatusChip tone="err">{errors.length} to fix</StatusChip>
    ) : warns.length > 0 ? (
      <StatusChip tone="warn">{warns.length} note{warns.length > 1 ? "s" : ""}</StatusChip>
    ) : empty ? (
      <StatusChip tone="outline">Skipped</StatusChip>
    ) : (
      <StatusChip tone="ok">Ready</StatusChip>
    );
  return (
    <Section
      className="review-sec"
      title={
        <>
          {title} {chip}
        </>
      }
      actions={
        <button type="button" className="btn sm ghost" aria-label={`Edit ${title}`} onClick={() => onEdit(step)}>
          <Pencil size={12} /> Edit
        </button>
      }
    >
      <div className="stack gap-3">
        {children}
        {issues.length > 0 && (
          <ul className="issue-list">
            {issues.map((i, n) => (
              <li key={n} className={i.level}>
                {i.text}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

/** One line at the top: ready, or which steps still need a fix (each a shortcut to that step). */
function Readiness({ all, tasks, stoppedAt, onEdit }: { all: Record<StepId, Issue[]>; tasks: number; stoppedAt?: string; onEdit: (s: StepId) => void }) {
  const broken = STEPS.map((s) => ({ ...s, n: errorsIn(all[s.id]).length })).filter((s) => s.n > 0);
  const total = broken.reduce((n, s) => n + s.n, 0);
  if (total === 0 && stoppedAt) {
    return (
      <Callout kind="err" title="The last attempt stopped at a failed step">
        “{stoppedAt}” was refused by the server; the reason is next to it at the bottom of this page. Fix it, then retry. Steps that already created something aren't repeated.
      </Callout>
    );
  }
  if (total === 0) {
    return (
      <Callout kind="ok" title="Everything checks out">
        Complete setup runs {tasks} step{tasks === 1 ? "" : "s"} through the regular API, in the order listed at the bottom of this page.
      </Callout>
    );
  }
  return (
    <Callout kind="err" title={`${total} problem${total === 1 ? "" : "s"} to fix before applying`}>
      <div className="row wrap gap-2 mt-2">
        {broken.map((s) => (
          <button key={s.id} type="button" className="btn sm" onClick={() => onEdit(s.id)}>
            <Pencil size={12} /> {s.label} · {s.n}
          </button>
        ))}
      </div>
    </Callout>
  );
}

export function ReviewStep({ all, user, commit, onEdit, ...p }: StepProps & { all: Record<StepId, Issue[]>; user: User | null; commit: Commit; onEdit: (s: StepId) => void }) {
  const { draft, server } = p;
  const profiles = server.profiles ?? [];
  const archive = profiles.find((x) => x.id === draft.archiveProfileId);
  const spec = archive ? effectiveSpec(draft, archive) : null;
  const preview = useProfilePreview(spec, !!spec);
  const pname = (id: number | null) => profiles.find((x) => x.id === id)?.name ?? "—";
  const quiet = effectiveQuietHours(draft, server.settings);
  const url = effectivePublicUrl(draft, server.settings);
  const progress = commit.tasks.length ? (commit.done / commit.tasks.length) * 100 : 0;
  const stages = [...draft.aging.stages].sort((a, b) => a.min_days - b.min_days);
  const agingLibs = draft.libraries.filter((l) => !draft.aging.excluded.includes(l.key));

  return (
    <StepFrame
      step="review"
      title="Review and apply"
      lede="Nothing after the account has been saved yet. Check the summary, then apply it in one go. If a step fails, fix it and apply again; anything already created isn't created twice."
      guide={
        <>
          <GuideSection title="After setup">
            <ul>
              <li>Each new library starts its first scan right away. Large libraries take a while to analyze.</li>
              <li>{draft.automation === "auto" ? "Matching files are queued when a scan finishes." : "Automation is off: the Files page shows what each rule would do, and nothing is queued."}</li>
              <li>Everything here can be changed later on the Libraries, Profiles, Rules, Nodes and Settings pages.</li>
            </ul>
          </GuideSection>
          <GuideSection title="How applying works">
            <div>Each step is a regular API call, run in order. The first failure stops the run and shows the server's message next to that step. Libraries and rules that were already created are remembered, so a retry doesn't create them again.</div>
          </GuideSection>
        </>
      }
    >
      <Readiness all={all} tasks={commit.tasks.length} stoppedAt={commit.tasks.find((t) => commit.status[t.id]?.state === "failed")?.label} onEdit={onEdit} />

      <ReviewCard title="Administrator" step="admin" issues={all.admin} onEdit={onEdit}>
        <dl className="kv">
          <dt>Account</dt>
          <dd>{user ? <span className="mono">{user.username}</span> : "Not created"}</dd>
          <dt>Access</dt>
          <dd>Local sign-in, argon2id password hash, 30-day sessions unless FF_SESSION_DAYS is set</dd>
        </dl>
      </ReviewCard>

      <ReviewCard title="Libraries" step="storage" issues={all.storage} onEdit={onEdit} empty={draft.libraries.length === 0}>
        {draft.libraries.length === 0 ? (
          <div className="muted small">No libraries will be created.</div>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Library</th>
                  <th>Folders</th>
                  <th>After converting</th>
                  <th>Scan</th>
                  <th>Size check</th>
                </tr>
              </thead>
              <tbody>
                {draft.libraries.map((l) => {
                  const v = l.validation;
                  return (
                    <tr key={l.key}>
                      <td className="strong">
                        {l.name || <span className="faint">Unnamed</span>} {`library:${l.key}` in draft.committed && <StatusChip tone="ok">Created</StatusChip>}
                      </td>
                      <td className="mono small">{l.paths.filter((x) => x.trim()).join(", ")}</td>
                      <td className="small">
                        {storageTitle(l.storage)}
                        {usesOutputFolder(l.storage) && l.output_path && <span className="faint mono"> → {l.output_path}</span>}
                        {usesBackupFolder(l.storage) && l.backup_path && <span className="faint mono"> → {l.backup_path}</span>}
                      </td>
                      <td className="mono small">{l.scan_interval_minutes ?? "?"} min</td>
                      <td className="small">{v.max_size_ratio == null ? <span className="text-warn">off</span> : `≤ ${Math.round(v.max_size_ratio * 100)}% · ${v.fail_if_larger ? "reject" : "warn only"}`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="small">
          <span className="faint">Automation: </span>
          {draft.automation === "auto" ? "Queue matching files automatically" : "Observe first (nothing is queued)"}
        </div>
      </ReviewCard>

      <ReviewCard title="Default transcoding profile" step="transcoding" issues={all.transcoding} onEdit={onEdit} empty={!archive}>
        {archive && spec ? (
          <dl className="kv">
            <dt>Profile</dt>
            <dd className="row wrap gap-2">
              <b>{archive.name}</b> <CodecChip codec={spec.video_codec} /> <span className="mono small">{spec.container.toUpperCase()}</span>
              {draft.profileEdits[archive.id] ? <StatusChip tone="accent">Modified</StatusChip> : <span className="faint small">unchanged</span>}
            </dd>
            {spec.video_codec !== "copy" && (
              <>
                <dt>Quality</dt>
                <dd>
                  {spec.rate_control === "quality" ? <span className="mono">{spec.quality} / 100</span> : "exact value from the profile"}
                  {preview.data && preview.data.quality.length > 0 && (
                    <span className="row wrap gap-1 mt-1">
                      {preview.data.quality
                        .filter((q) => q.backend !== "amf")
                        .map((q) => (
                          <span key={q.backend} className="chip outline mono">
                            {q.encoder} {q.param} {q.value}
                          </span>
                        ))}
                    </span>
                  )}
                </dd>
                <dt>Hardware</dt>
                <dd>
                  {spec.hw_mode === "auto" ? "Auto" : spec.hw_mode.toUpperCase()} · CPU fallback {spec.allow_cpu_fallback || spec.hw_mode === "cpu" ? "on" : "off"} · speed {spec.speed}
                </dd>
              </>
            )}
            <dt>Keeps</dt>
            <dd className="small">
              {[spec.keep_metadata && "metadata", spec.keep_chapters && "chapters", spec.keep_subtitles && "subtitles", spec.keep_attachments && "attachments", spec.ten_bit === "auto" && "10-bit/HDR"].filter(Boolean).join(", ") || "only video and audio"} · audio {spec.audio_mode.replace("_", " ")}
            </dd>
          </dl>
        ) : (
          <div className="muted small">No profile selected.</div>
        )}
      </ReviewCard>

      <ReviewCard title="Rules" step="rules" issues={all.rules} onEdit={onEdit} empty={!draft.aging.enabled && draft.exceptions.length === 0}>
        {draft.exceptions.map((r, i) => (
          <div key={r.key} className="rule-summary">
            <span className="mono faint">R{i + 1}</span>
            <span>
              <b>{r.name || "Unnamed rule"}</b>
              <span className="faint"> · {r.library_key ? draft.libraries.find((l) => l.key === r.library_key)?.name : "all libraries"}</span>
              <br />
              <span className="kw-inline">IF</span> {r.conditions.map((c) => describeCondition(c, server.fields)).join(" AND ") || "—"} <span className="kw-inline">THEN</span>{" "}
              {r.action === "skip" ? "leave it alone" : `transcode using ${pname(r.profile_id)} (${PRIORITIES[r.priority]})`}
            </span>
          </div>
        ))}
        {draft.aging.enabled ? (
          <div className="rule-summary">
            <span className="mono faint">AP</span>
            <span>
              <b>Aging policy</b>
              <span className="faint"> · {agingLibs.map((l) => l.name).join(", ") || "no library"} · age from {draft.aging.age_field === "file_age_days" ? "file modified date" : "recording date"}</span>
              {stages.map((s, i) => (
                <span key={i} className="stage-line">
                  <span className="mono">{stages[i + 1] ? `${s.min_days}–${stages[i + 1].min_days}` : `${s.min_days}+`} d</span> {s.action === "keep" ? "keep original" : `${pname(s.profile_id)} (${PRIORITIES[s.priority]})`}
                </span>
              ))}
            </span>
          </div>
        ) : (
          <div className="muted small">No aging policy.</div>
        )}
      </ReviewCard>

      <ReviewCard title="Nodes" step="nodes" issues={all.nodes} onEdit={onEdit}>
        <dl className="kv">
          {(server.nodes ?? []).map((n) => (
            <NodeLine key={n.id} name={n.name} value={`${n.status} · ${accelSummary(n).join(", ") || (n.capabilities ? "CPU encoding only" : "no hardware report yet")}${nodeDraft(draft, n).path_mappings.filter((m) => m.server && m.node).length ? ` · ${nodeDraft(draft, n).path_mappings.filter((m) => m.server && m.node).length} path mapping(s)` : ""}`} />
          ))}
          <dt>Server URL</dt>
          <dd className="mono small">{url.trim() || <span className="faint">empty: the address you browse from</span>}</dd>
        </dl>
      </ReviewCard>

      <ReviewCard title="Schedule" step="schedule" issues={all.schedule} onEdit={onEdit}>
        <dl className="kv">
          <dt>Background work</dt>
          <dd>{quiet?.enabled ? `${quiet.applies_to === "background" ? "Background" : "Low + Background"} jobs only ${describeWindow(quiet.window)}` : "No restriction"}</dd>
          {draft.aging.enabled && (
            <>
              <dt>Aging window</dt>
              <dd>{draft.policyWindow ? describeWindow(draft.policyWindow) : "Any time"}</dd>
            </>
          )}
          {(server.nodes ?? []).map((n) => {
            const nd = nodeDraft(draft, n);
            const limits = [nd.max_gpu_util != null && `GPU < ${nd.max_gpu_util}%`, nd.max_cpu_util != null && `CPU < ${nd.max_cpu_util}%`].filter(Boolean).join(", ");
            return <NodeLine key={n.id} name={n.name} value={`${nd.max_concurrency ?? "?"} job(s) at once${nd.reserve_slot_for_normal ? " (one kept for Normal+)" : ""} · ${nd.window ? describeWindow(nd.window) : "any time"}${limits ? ` · start only if ${limits}` : ""}`} />;
          })}
        </dl>
      </ReviewCard>

      <Section
        id="setup-apply"
        className="apply-panel"
        title="Apply configuration"
        description="Runs in this order when you choose Complete setup."
        actions={
          <span className="mono small faint">
            {commit.done}/{commit.tasks.length}
          </span>
        }
        flush
      >
        <div className={`apply-progress ${commit.running || commit.failed ? "on" : ""}`} aria-hidden={!(commit.running || commit.failed)}>
          <Timeline value={progress} state={commit.failed ? "err" : undefined} thin />
        </div>
        <div>
          {commit.tasks.length === 0 ? (
            <div className="panel-body muted small">Nothing to apply. Complete setup finishes right away.</div>
          ) : (
            <ol className="task-list">
              {commit.tasks.map((t) => {
                const st = commit.status[t.id] ?? (t.id in draft.committed ? { state: "done" as const, note: "Already created" } : undefined);
                return (
                  <li key={t.id} className={st?.state ?? "pending"}>
                    <span className="ti">
                      {st?.state === "done" ? <Check size={14} /> : st?.state === "running" ? <Loader2 size={14} className="spin" /> : st?.state === "failed" ? <XCircle size={14} /> : <Circle size={11} />}
                    </span>
                    <span className="min0">
                      <span>{t.label}</span>
                      {st?.note && <span className="faint small"> · {st.note}</span>}
                      {st?.error && <span className="task-error">{st.error}</span>}
                    </span>
                    <span className="ep">{t.endpoint}</span>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </Section>
      {commit.failed && (
        <div className="appear" role="alert">
          <Callout kind="err" title="Setup stopped at a failed step">
            Nothing after it ran. Fix the cause (the message is next to the step), then choose Retry & complete setup.
          </Callout>
        </div>
      )}
    </StepFrame>
  );
}

function NodeLine({ name, value }: { name: string; value: string }) {
  return (
    <>
      <dt className="ellipsis" title={name}>
        {name}
      </dt>
      <dd className="small">{value}</dd>
    </>
  );
}
