import { Eye, Play, Plus, X } from "lucide-react";
import type { AgingStage, ConditionLeaf, FieldDef, Profile } from "../../api/types";
import { Callout, ConfirmButton, NumberInput, Segmented, Toggle } from "../../components/ui";
import { PRIORITIES } from "../../lib/format";
import { ConditionRow } from "../Rules";
import { MAX_AGE_DAYS, MAX_AGING_STAGES, newKey, type ExceptionDraft, type Issue, type SetupDraft } from "./model";
import { GuideSection, IssueList, Panel, StepFrame, type StepProps } from "./parts";

const AGE_LABEL = { file_age_days: "File age", recorded_age_days: "Recording age" } as const;

function nextAbove(stages: AgingStage[], days: number): number | null {
  const higher = stages.map((s) => s.min_days).filter((d) => d > days);
  return higher.length ? Math.min(...higher) : null;
}

export function RulesStep({ draft, update, server, issues, showErrors }: StepProps) {
  const profiles = server.profiles ?? [];
  const aging = draft.aging;
  const setAging = (patch: Partial<SetupDraft["aging"]>) => update((d) => ({ ...d, aging: { ...d.aging, ...patch } }));
  const agingIssues = issues.filter((i) => !i.key);

  return (
    <StepFrame
      step="rules"
      title="Decide when footage is compressed"
      lede="The aging policy keeps recent footage untouched and converts it once it's old enough. Rules decide what happens to each file after a scan: they run top to bottom, the first match decides, and a file nothing matches is left alone."
      guide={<RulesGuide />}
    >
      <Panel
        title="Aging policy"
        description={<PolicySentence draft={draft} profiles={profiles} />}
        right={<Toggle checked={aging.enabled} onChange={(v) => setAging({ enabled: v })} label={aging.enabled ? "On" : "Off"} />}
      >
        {!aging.enabled ? (
          <div className="muted small">No aging policy will be created. You can set one up later under Rules → Aging policy.</div>
        ) : (
          <AgingEditor draft={draft} profiles={profiles} setAging={setAging} issues={agingIssues} showErrors={showErrors} />
        )}
      </Panel>

      <Panel
        title="Rules checked before the aging policy"
        description="Optional. Protect files, or convert particular sources on their own terms. They're checked first, and every condition in a rule must match."
        right={draft.exceptions.length > 0 && <span className="chip">{draft.exceptions.length}</span>}
      >
        {!server.fields ? (
          <div className="faint small">Loading rule fields…</div>
        ) : (
          <>
            {draft.exceptions.map((r, i) => (
              <ExceptionEditor key={r.key} rule={r} index={i} draft={draft} update={update} fields={server.fields!} profiles={profiles} issues={issues.filter((x) => x.key === r.key)} showErrors={showErrors} />
            ))}
            <div className="row wrap gap-2">
              <span className="faint small">Add:</span>
              {presets(draft).map((pr) => (
                <button key={pr.label} type="button" className="btn sm" title={pr.hint} onClick={() => update((d) => ({ ...d, exceptions: [...d.exceptions, pr.make()] }))}>
                  <Plus size={12} /> {pr.label}
                </button>
              ))}
            </div>
          </>
        )}
      </Panel>

      <Panel title="When a rule matches a file" description="Start by watching what FrameForge would do, or let it queue work right away.">
        <div className="option-cards" role="radiogroup" aria-label="Automation">
          <button type="button" role="radio" aria-checked={draft.automation === "observe"} className={`option-card ${draft.automation === "observe" ? "on" : ""}`} onClick={() => update((d) => ({ ...d, automation: "observe" }))}>
            <span className="t">
              <Eye size={14} /> Observe first
            </span>
            <span className="d">Scan and evaluate, but queue nothing. Each file shows what would happen and why on the Files page. Turn on “Create jobs automatically” per library when the decisions look right.</span>
          </button>
          <button type="button" role="radio" aria-checked={draft.automation === "auto"} className={`option-card ${draft.automation === "auto" ? "on" : ""}`} onClick={() => update((d) => ({ ...d, automation: "auto" }))}>
            <span className="t">
              <Play size={14} /> Queue jobs automatically
            </span>
            <span className="d">Matching files are queued when the first scan finishes. Every file already past a stage's age matches at once, which on a large library can be thousands of jobs.</span>
          </button>
        </div>
        {draft.automation === "auto" && draft.libraries.some((l) => l.storage.original_handling === "delete") && (
          <Callout kind="warn" title="Originals will be deleted automatically">
            A library deletes originals right after a verified conversion. Consider testing a profile on a few files first, or keep originals for a few days while you check the results.
          </Callout>
        )}
      </Panel>
    </StepFrame>
  );
}

function AgingEditor({ draft, profiles, setAging, issues, showErrors }: { draft: SetupDraft; profiles: Profile[]; setAging: (p: Partial<SetupDraft["aging"]>) => void; issues: Issue[]; showErrors: boolean }) {
  const aging = draft.aging;
  const stages = aging.stages;
  const sorted = [...stages].sort((a, b) => a.min_days - b.min_days);
  const name = (id: number | null) => profiles.find((x) => x.id === id)?.name ?? "Choose a profile";
  const setStage = (i: number, patch: Partial<AgingStage>) => setAging({ stages: stages.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const archive = draft.archiveProfileId ?? profiles[0]?.id ?? null;
  const longTerm = profiles.find((x) => x.spec.video_codec === "av1" && x.id !== archive);
  const last = sorted[sorted.length - 1]?.min_days ?? 0;
  const ageLabel = AGE_LABEL[aging.age_field];

  return (
    <>
      {sorted.length > 0 && (
        <div className="aging" aria-label="Aging timeline">
          {sorted.map((s, i) => {
            const next = sorted[i + 1];
            return (
              <div key={i} className={`stage ${s.action === "keep" ? "keep" : ""}`}>
                <span className="range">{next ? `${s.min_days}–${next.min_days} days` : `${s.min_days}+ days`}</span>
                <span className="what">{s.action === "keep" ? "Keep original" : name(s.profile_id)}</span>
                {s.action === "transcode" && <span className="faint small">{PRIORITIES[s.priority]} priority</span>}
              </div>
            );
          })}
        </div>
      )}

      <div className="stage-list">
        {stages.map((s, i) => {
          const next = nextAbove(stages, s.min_days);
          return (
            <div key={i} className="stage-row">
              <span className="stage-n mono">S{i + 1}</span>
              <div className="rule-lines">
                <div className="rule-line">
                  <span className="kw">IF</span>
                  <div className="row wrap">
                    <span className="token">{ageLabel}</span>
                    <span className="muted">is at least</span>
                    <div className="w-num-sm">
                      <NumberInput value={s.min_days} onChange={(v) => setStage(i, { min_days: v ?? 0 })} min={0} max={MAX_AGE_DAYS} suffix="days" />
                    </div>
                    {next != null ? (
                      <span className="muted">
                        and less than <b className="mono">{next}</b> days
                      </span>
                    ) : (
                      <span className="faint">with no upper limit</span>
                    )}
                  </div>
                </div>
                <div className="rule-line">
                  <span className="kw">THEN</span>
                  <div className="row wrap">
                    <Segmented
                      value={s.action}
                      onChange={(v) => setStage(i, { action: v, profile_id: v === "transcode" ? s.profile_id ?? archive : null })}
                      options={[
                        { value: "keep", label: "Keep original" },
                        { value: "transcode", label: "Transcode using" },
                      ]}
                    />
                    {s.action === "transcode" && (
                      <>
                        <select className="select w-select" aria-label={`Stage ${i + 1} profile`} value={s.profile_id ?? ""} onChange={(e) => setStage(i, { profile_id: e.target.value ? Number(e.target.value) : null })}>
                          {s.profile_id == null && <option value="">Choose a profile</option>}
                          {profiles.map((pr) => (
                            <option key={pr.id} value={pr.id}>
                              {pr.name}
                            </option>
                          ))}
                        </select>
                        <span className="muted small">at</span>
                        <select className="select w-select-sm" aria-label={`Stage ${i + 1} priority`} value={s.priority} onChange={(e) => setStage(i, { priority: Number(e.target.value) })}>
                          {PRIORITIES.map((label, k) => (
                            <option key={label} value={k}>
                              {label}
                            </option>
                          ))}
                        </select>
                        <span className="muted small">priority</span>
                      </>
                    )}
                  </div>
                </div>
              </div>
              {stages.length > 1 && (
                <button type="button" className="btn icon ghost" aria-label={`Remove stage ${i + 1}`} title="Remove stage" onClick={() => setAging({ stages: stages.filter((_, j) => j !== i) })}>
                  <X size={14} />
                </button>
              )}
            </div>
          );
        })}
      </div>

      <div className="row wrap">
        <button type="button" className="btn sm ghost" disabled={stages.length >= MAX_AGING_STAGES} onClick={() => setAging({ stages: [...stages, { min_days: last ? last * 2 : 90, action: "transcode", profile_id: archive, priority: 1 }] })}>
          <Plus size={12} /> Add stage
        </button>
        {longTerm && !stages.some((s) => s.profile_id === longTerm.id) && (
          <button type="button" className="btn sm ghost" disabled={stages.length >= MAX_AGING_STAGES} onClick={() => setAging({ stages: [...stages, { min_days: Math.max(365, last + 1), action: "transcode", profile_id: longTerm.id, priority: 1 }] })}>
            <Plus size={12} /> Long-term stage with {longTerm.name}
          </button>
        )}
      </div>

      <div className="form-grid">
        <div className="field">
          <span className="field-label">Age is measured from</span>
          <Segmented
            value={aging.age_field}
            onChange={(v) => setAging({ age_field: v })}
            options={[
              { value: "file_age_days", label: "File modified date" },
              { value: "recorded_age_days", label: "Recording date (metadata)" },
            ]}
          />
          <div className="help">
            {aging.age_field === "file_age_days"
              ? "Days since the file was last modified. Converted files keep the original's date, so the clock doesn't restart."
              : "The creation time stored in the file, falling back to the modified date. Use it when files were copied recently and their dates are misleading."}
          </div>
        </div>
        {draft.libraries.length > 0 && (
          <div className="field">
            <span className="field-label">Create the policy for</span>
            <div className="row wrap gap-3">
              {draft.libraries.map((l) => (
                <Toggle
                  key={l.key}
                  checked={!aging.excluded.includes(l.key)}
                  onChange={(on) => setAging({ excluded: on ? aging.excluded.filter((k) => k !== l.key) : [...aging.excluded, l.key] })}
                  label={l.name.trim() || "Unnamed library"}
                />
              ))}
            </div>
            <div className="help">Aging policies belong to one library each. The same stages are saved for every library that's switched on.</div>
          </div>
        )}
      </div>
      <IssueList issues={issues} showErrors={showErrors} />
      <div className="faint small">Stages match on age only. Files already in a stage's target codec are skipped, unless the profile also lowers resolution or frame rate. To limit a stage to certain codecs, add a rule below.</div>
    </>
  );
}

function ExceptionEditor({
  rule,
  index,
  draft,
  update,
  fields,
  profiles,
  issues,
  showErrors,
}: {
  rule: ExceptionDraft;
  index: number;
  draft: SetupDraft;
  update: StepProps["update"];
  fields: FieldDef[];
  profiles: Profile[];
  issues: Issue[];
  showErrors: boolean;
}) {
  const committed = `rule:${rule.key}` in draft.committed;
  const set = (patch: Partial<ExceptionDraft>) => update((d) => ({ ...d, exceptions: d.exceptions.map((x) => (x.key === rule.key ? { ...x, ...patch } : x)) }));
  const setCond = (i: number, c: ConditionLeaf) => set({ conditions: rule.conditions.map((x, j) => (j === i ? c : x)) });
  const remove = () => update((d) => ({ ...d, exceptions: d.exceptions.filter((x) => x.key !== rule.key) }));

  if (committed) {
    return (
      <div className="rule-card">
        <div className="row">
          <span className="mono faint">R{index + 1}</span>
          <b>{rule.name}</b>
          <span className="spacer" />
          <span className="chip caps ok">Created</span>
        </div>
        <div className="faint small">Already saved on the server. Edit it later on the Rules page.</div>
      </div>
    );
  }

  return (
    <div className="rule-card">
      <div className="row wrap">
        <span className="mono faint">R{index + 1}</span>
        <input className="input w-wide" aria-label={`Rule ${index + 1} name`} value={rule.name} onChange={(e) => set({ name: e.target.value })} placeholder="Rule name" />
        <select className="select w-select" aria-label={`Rule ${index + 1} scope`} value={rule.library_key ?? ""} onChange={(e) => set({ library_key: e.target.value || null })}>
          <option value="">All libraries</option>
          {draft.libraries.map((l) => (
            <option key={l.key} value={l.key}>
              {l.name.trim() || "Unnamed library"}
            </option>
          ))}
        </select>
        <span className="spacer" />
        <ConfirmButton className="btn sm ghost danger" confirmText="Remove rule?" confirmLabel="Remove" onConfirm={remove}>
          <X size={12} /> Remove
        </ConfirmButton>
      </div>
      <div className="rule-lines">
        {rule.conditions.map((c, i) => (
          <ConditionRow key={i} join={i === 0 ? "IF" : "AND"} cond={c} fields={fields} onChange={(x) => setCond(i, x)} onRemove={() => set({ conditions: rule.conditions.filter((_, j) => j !== i) })} />
        ))}
        <div className="rule-line">
          <span className="kw">{rule.conditions.length ? "" : "IF"}</span>
          <div>
            <button type="button" className="btn sm ghost" onClick={() => set({ conditions: [...rule.conditions, { type: "condition", field: "video_codec", operator: "is", value: "h264" }] })}>
              <Plus size={12} /> Condition
            </button>
          </div>
        </div>
        <div className="rule-line">
          <span className="kw">THEN</span>
          <div className="row wrap">
            <Segmented
              value={rule.action}
              onChange={(v) => set({ action: v, profile_id: v === "transcode" ? rule.profile_id ?? draft.archiveProfileId : null })}
              options={[
                { value: "skip", label: "Leave it alone" },
                { value: "transcode", label: "Transcode using" },
              ]}
            />
            {rule.action === "transcode" && (
              <>
                <select className="select w-select" aria-label={`Rule ${index + 1} profile`} value={rule.profile_id ?? ""} onChange={(e) => set({ profile_id: e.target.value ? Number(e.target.value) : null })}>
                  {rule.profile_id == null && <option value="">Choose a profile</option>}
                  {profiles.map((pr) => (
                    <option key={pr.id} value={pr.id}>
                      {pr.name}
                    </option>
                  ))}
                </select>
                <span className="muted small">at</span>
                <select className="select w-select-sm" aria-label={`Rule ${index + 1} priority`} value={rule.priority} onChange={(e) => set({ priority: Number(e.target.value) })}>
                  {PRIORITIES.map((label, k) => (
                    <option key={label} value={k}>
                      {label}
                    </option>
                  ))}
                </select>
                <span className="muted small">priority</span>
              </>
            )}
          </div>
        </div>
      </div>
      <IssueList issues={issues} showErrors={showErrors} />
    </div>
  );
}

function presets(d: SetupDraft): { label: string; hint: string; make: () => ExceptionDraft }[] {
  const base = (name: string, conditions: ConditionLeaf[], action: ExceptionDraft["action"]): ExceptionDraft => ({
    key: newKey("rule"),
    name,
    library_key: null,
    conditions,
    action,
    profile_id: action === "transcode" ? d.archiveProfileId : null,
    priority: 2,
  });
  const cond = (field: string, operator: string, value: unknown): ConditionLeaf => ({ type: "condition", field, operator, value });
  const days = d.aging.stages.find((s) => s.action === "transcode")?.min_days ?? 30;
  return [
    { label: "Leave AV1 files alone", hint: "Stops an H.265 stage from re-encoding files that are already AV1", make: () => base("Leave AV1 files alone", [cond("video_codec", "is", "av1")], "skip") },
    { label: "Protect a folder", hint: "Files under a matching path are never converted", make: () => base("Protect edits folder", [cond("path", "contains", "/edits/")], "skip") },
    {
      label: `Compress H.264 after ${days} days`,
      hint: "Converts only H.264 sources once they reach this age",
      make: () => base(`Compress H.264 after ${days} days`, [cond("file_age_days", "gte", days), cond("video_codec", "is", "h264")], "transcode"),
    },
    { label: "Custom rule", hint: "Start from one condition", make: () => base("New rule", [cond("file_age_days", "gt", days)], "skip") },
  ];
}

/** The aging policy in plain words, e.g. "Footage stays untouched for 30 days, then …". */
function PolicySentence({ draft, profiles }: { draft: SetupDraft; profiles: Profile[] }) {
  const convert = [...draft.aging.stages].filter((s) => s.action === "transcode").sort((a, b) => a.min_days - b.min_days);
  const name = (id: number | null) => profiles.find((x) => x.id === id)?.name ?? "the chosen profile";
  if (!draft.aging.enabled) return <>Off: nothing is converted because of its age.</>;
  if (convert.length === 0) return <>No stage converts anything yet.</>;
  return (
    <>
      Footage stays untouched for <b className="strong">{convert[0].min_days} days</b>, then eligible files are converted with <b className="strong">{name(convert[0].profile_id)}</b>
      {convert.slice(1).map((s) => (
        <span key={s.min_days}>
          ; after <b className="strong">{s.min_days} days</b>, again with <b className="strong">{name(s.profile_id)}</b>
        </span>
      ))}
      .
    </>
  );
}

function RulesGuide() {
  return (
    <>
      <GuideSection title="Which files are eligible">
        <div>A stage converts a file that isn't already in the target codec (unless the profile also lowers resolution or frame rate) and wasn't already processed with that profile.</div>
      </GuideSection>
      <GuideSection title="Re-processing guard">
        <div>A matching file is still skipped when it was already processed with the rule's profile. FrameForge checks three places:</div>
        <ul>
          <li>its database record,</li>
          <li>the FRAMEFORGE tag written into the file,</li>
          <li>the fingerprints of originals and outputs of finished jobs, which catches moves and renames.</li>
        </ul>
        <div>A previous failed attempt with the same profile also stops re-queueing until you retry it. Stages with different profiles (H.265, later AV1) still apply, by design.</div>
      </GuideSection>
      <GuideSection title="Order matters">
        <div>“Leave it alone” rules belong at the top. After setup, reorder rules and see why each file was or wasn't converted on the Rules and Files pages.</div>
      </GuideSection>
    </>
  );
}
