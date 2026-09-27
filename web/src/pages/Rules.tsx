import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Pencil, Plus, Trash2, Workflow, X } from "lucide-react";
import { api } from "../api/client";
import { useAction, useAging, useLibraries, useProfiles, useRuleFields, useRules } from "../api/hooks";
import type { AgingPolicy, AgingStage, ConditionGroup, ConditionLeaf, FieldDef, Rule } from "../api/types";
import { PriorityChip } from "../components/media";
import { Callout, ConfirmButton, Empty, ErrorState, Field, LoadingState, Menu, Modal, NumberInput, PageHeader, Section, Segmented, SettingRow, Toggle, useToast } from "../components/ui";
import { bytes, PRIORITIES } from "../lib/format";

export function RulesPage() {
  const { data: rules, error, refetch } = useRules();
  const { data: libraries } = useLibraries();
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const toast = useToast();
  const reorder = useAction((ids: number[]) => api.post("/rules/reorder", { ids }), [["rules"]]);
  const toggle = useAction((r: Rule) => api.put(`/rules/${r.id}`, { ...ruleBody(r), enabled: !r.enabled }), [["rules"]]);
  const del = useAction((id: number) => api.del(`/rules/${id}`), [["rules"]]);
  const libName = (id: number | null) => (id == null ? "All libraries" : libraries?.find((l) => l.id === id)?.name ?? "?");

  const move = (index: number, dir: -1 | 1) => {
    if (!rules) return;
    const ids = rules.map((r) => r.id);
    const j = index + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[index], ids[j]] = [ids[j], ids[index]];
    reorder.mutate(ids);
  };

  return (
    <div className="page">
      <PageHeader
        title="Rules"
        description="Rules decide which files get converted, and how. The aging policy covers the common case; add your own rules for exceptions."
        actions={
          <button className="btn primary" onClick={() => setEditing("new")}>
            <Plus size={16} /> New rule
          </button>
        }
      />

      <AgingPolicyPanel />

      <Section title="All rules" description="Checked top to bottom on every scan. The first rule that matches a file decides what happens to it." flush>
        {error && !rules ? (
          <ErrorState title="Couldn't load rules" error={error} onRetry={() => refetch()} />
        ) : !rules ? (
          <LoadingState rows={1} />
        ) : rules.length === 0 ? (
          <Empty
            icon={<Workflow size={20} />}
            title="No rules yet"
            action={
              <button className="btn" onClick={() => setEditing("new")}>
                <Plus size={14} /> New rule
              </button>
            }
          >
            Set up the aging policy above, or build your own rule.
          </Empty>
        ) : (
          <div className="table-scroll">
            <table className="table rules-table">
              <thead>
                <tr>
                  <th className="col-num">#</th>
                  <th>When</th>
                  <th>Then</th>
                  <th>Applies to</th>
                  <th className="col-on">On</th>
                  <th className="col-actions">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r, i) => (
                  <tr key={r.id} className={r.enabled ? undefined : "dim"}>
                    <td className="mono faint col-num">{i + 1}</td>
                    <td className="cell-rule">
                      <div className="row gap-2 wrap">
                        <span className="primary-cell">{r.name}</span>
                        {r.policy_group && <span className="chip outline">aging policy</span>}
                      </div>
                      <div className="faint small">{describeGroup(r.conditions)}</div>
                    </td>
                    <td>
                      {r.action === "skip" ? (
                        <span className="chip">Leave as is</span>
                      ) : (
                        <span className="row wrap gap-1">
                          <span className="chip accent">{r.profile_name}</span>
                          <PriorityChip priority={r.priority} />
                          {r.schedule.window && (
                            <span className="chip outline mono">
                              {r.schedule.window.start}–{r.schedule.window.end}
                            </span>
                          )}
                        </span>
                      )}
                    </td>
                    <td className="small muted nowrap">{libName(r.library_id)}</td>
                    <td className="col-on">
                      <Toggle checked={r.enabled} onChange={() => toggle.mutate(r)} />
                    </td>
                    <td className="col-actions">
                      <div className="row end gap-1">
                        <button className="btn sm ghost icon" disabled={i === 0} onClick={() => move(i, -1)} title="Move up" aria-label="Move up">
                          <ArrowUp size={14} />
                        </button>
                        <button className="btn sm ghost icon" disabled={i === rules.length - 1} onClick={() => move(i, 1)} title="Move down" aria-label="Move down">
                          <ArrowDown size={14} />
                        </button>
                        <button className="btn sm ghost icon" onClick={() => setEditing(r)} title="Edit" aria-label="Edit">
                          <Pencil size={14} />
                        </button>
                        <Menu
                          label={`More actions for ${r.name}`}
                          items={[
                            {
                              label: "Delete rule",
                              icon: <Trash2 size={14} />,
                              danger: true,
                              confirm: { text: `Delete “${r.name}”?`, detail: "Queued and finished jobs are not affected.", label: "Delete" },
                              onSelect: () => del.mutate(r.id, { onSuccess: () => toast("Rule deleted") }),
                            },
                          ]}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {editing && <RuleEditor rule={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function ruleBody(r: Rule) {
  return {
    name: r.name,
    description: r.description,
    library_id: r.library_id,
    enabled: r.enabled,
    conditions: r.conditions,
    action: r.action,
    profile_id: r.profile_id,
    priority: r.priority,
    schedule: r.schedule,
    skip_if_target_codec: r.skip_if_target_codec,
  };
}

const OP_TEXT: Record<string, string> = {
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  eq: "=",
  neq: "≠",
  is: "is",
  is_not: "is not",
  in: "in",
  not_in: "not in",
  between: "between",
  contains: "contains",
  not_contains: "doesn't contain",
  starts_with: "starts with",
  ends_with: "ends with",
  glob: "matches",
  matches: "matches",
  is_true: "is yes",
  is_false: "is no",
};

const FIELD_SHORT: Record<string, string> = {
  file_age_days: "age",
  recorded_age_days: "recording age",
  file_size_gb: "size",
  video_codec: "codec",
  resolution: "resolution",
  frame_rate: "fps",
};

function describeGroup(g: ConditionGroup): string {
  if (!g.children?.length) return "Every file";
  const join = g.op === "any" ? " or " : " and ";
  const parts = g.children.map((c) => {
    if (c.type === "group") return `(${describeGroup(c)})`;
    const v = Array.isArray(c.value) ? c.value.join(c.operator === "between" ? "–" : ", ") : c.value ?? "";
    const unit = c.field.endsWith("_days") ? " days" : c.field === "file_size_gb" ? " GB" : c.field === "resolution" ? "p" : "";
    return `${FIELD_SHORT[c.field] ?? c.field.replace(/_/g, " ")} ${OP_TEXT[c.operator] ?? c.operator} ${v}${unit}`.trim();
  });
  return (g.op === "none" ? "none of: " : "") + parts.join(join);
}

// ---------------------------------------------------------------------------
// Aging policy
// ---------------------------------------------------------------------------

function AgingPolicyPanel() {
  const { data: libraries } = useLibraries();
  const { data: profiles } = useProfiles();
  const [libraryId, setLibraryId] = useState<number | null>(null);
  const effectiveLib = libraryId ?? libraries?.[0]?.id ?? null;
  const { data } = useAging(effectiveLib);
  const [policy, setPolicy] = useState<AgingPolicy | null>(null);
  const toast = useToast();

  useEffect(() => {
    if (!data) return;
    setPolicy(
      data.policy ?? {
        library_id: effectiveLib,
        age_field: "file_age_days",
        stages: [
          { min_days: 0, action: "keep", profile_id: null, priority: 2 },
          { min_days: 90, action: "transcode", profile_id: (profiles?.find((p) => p.builtin_key === "balanced") ?? profiles?.find((p) => p.builtin_key === "youtube_archive") ?? profiles?.[0])?.id ?? null, priority: 2 },
        ],
        window: null,
        enabled: true,
      },
    );
  }, [data, effectiveLib, profiles]);

  const save = useAction((p: AgingPolicy) => api.put("/rules/aging/policy", { ...p, library_id: effectiveLib }), [["rules"], ["aging"]]);
  const remove = useAction(() => api.del(`/rules/aging/policy?library_id=${effectiveLib}`), [["rules"], ["aging"]]);
  const evaluate = useAction(() => api.post<{ jobs_created: number }>(`/libraries/${effectiveLib}/evaluate`), [["jobs"], ["files"], ["stats"]]);

  if (!libraries?.length) return null;
  if (!policy) return null;
  const setStage = (i: number, s: Partial<AgingStage>) => setPolicy({ ...policy, stages: policy.stages.map((x, j) => (j === i ? { ...x, ...s } : x)) });
  const profileName = (id: number | null) => profiles?.find((p) => p.id === id)?.name ?? "—";
  const sorted = [...policy.stages].sort((a, b) => a.min_days - b.min_days);

  return (
    <Section
      className="mb-section"
      title={
        <>
          Aging policy {data?.policy ? <span className="chip ok">Active</span> : <span className="chip">Not set up</span>}
        </>
      }
      description="Keep recent footage untouched and convert it in stages as it gets older."
      actions={
        libraries.length > 1 ? (
          <select className="select sm w-select" aria-label="Library" value={effectiveLib ?? ""} onChange={(e) => setLibraryId(Number(e.target.value))}>
            {libraries.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="muted small">{libraries[0].name}</span>
        )
      }
    >
      <div className="stack gap-5">
        <div className="aging" aria-label="Timeline of the aging stages">
          {sorted.map((s, i) => {
            const next = sorted[i + 1];
            return (
              <div key={i} className={`stage ${s.action === "keep" ? "keep" : ""}`}>
                <span className="range">{next ? `${s.min_days}–${next.min_days} days` : `${s.min_days}+ days`}</span>
                <span className="what">{s.action === "keep" ? "Keep original" : profileName(s.profile_id)}</span>
                {s.action === "transcode" && <span className="faint small">{PRIORITIES[s.priority]} priority</span>}
              </div>
            );
          })}
        </div>

        <div className="stage-editor">
          {policy.stages.map((s, i) => (
            <div className="stage-edit-row" key={i}>
              <span className="small muted stage-when">{i === 0 ? "From" : "After"}</span>
              <div className="w-num-sm">
                <NumberInput value={s.min_days} onChange={(v) => setStage(i, { min_days: v ?? 0 })} min={0} suffix="days" />
              </div>
              <Segmented
                value={s.action}
                onChange={(v) => setStage(i, { action: v, profile_id: v === "transcode" ? s.profile_id ?? profiles?.[0]?.id ?? null : null })}
                options={[
                  { value: "keep", label: "Keep" },
                  { value: "transcode", label: "Convert" },
                ]}
              />
              {s.action === "transcode" && (
                <>
                  <select className="select w-select" aria-label="Profile" value={s.profile_id ?? ""} onChange={(e) => setStage(i, { profile_id: Number(e.target.value) })}>
                    {profiles?.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <select className="select w-select-sm" aria-label="Priority" value={s.priority} onChange={(e) => setStage(i, { priority: Number(e.target.value) })}>
                    {PRIORITIES.map((p, k) => (
                      <option key={p} value={k}>
                        {p}
                      </option>
                    ))}
                  </select>
                </>
              )}
              {policy.stages.length > 1 && (
                <button className="btn icon ghost" title="Remove stage" aria-label="Remove stage" onClick={() => setPolicy({ ...policy, stages: policy.stages.filter((_, j) => j !== i) })}>
                  <X size={16} />
                </button>
              )}
            </div>
          ))}
          <div>
            <button
              className="btn sm ghost"
              onClick={() => {
                const last = sorted[sorted.length - 1]?.min_days ?? 0;
                setPolicy({ ...policy, stages: [...policy.stages, { min_days: last ? last * 2 : 90, action: "transcode", profile_id: profiles?.[0]?.id ?? null, priority: 1 }] });
              }}
            >
              <Plus size={14} /> Add stage
            </button>
          </div>
        </div>

        <div className="settings-list">
          <SettingRow label="Age is measured from" description="The recording date comes from the file's metadata, when it has one.">
            <Segmented
              value={policy.age_field}
              onChange={(v) => setPolicy({ ...policy, age_field: v })}
              options={[
                { value: "file_age_days", label: "File modified date" },
                { value: "recorded_age_days", label: "Recording date" },
              ]}
            />
          </SettingRow>
          <SettingRow label="Only run these jobs overnight" description="Between 23:00 and 07:00, server time.">
            <Toggle checked={!!policy.window} onChange={(v) => setPolicy({ ...policy, window: v ? { start: "23:00", end: "07:00", days: [0, 1, 2, 3, 4, 5, 6] } : null })} />
          </SettingRow>
        </div>

        <div className="row wrap form-actions">
          {data?.policy && (
            <ConfirmButton
              className="btn danger"
              confirmText="Remove the aging policy?"
              detail="Its rules are deleted. Queued and finished jobs are not affected."
              confirmLabel="Remove"
              onConfirm={() => remove.mutate(undefined, { onSuccess: () => toast("Aging policy removed") })}
            >
              Remove policy
            </ConfirmButton>
          )}
          <span className="spacer" />
          {data?.policy && (
            <button className="btn" title="Check every file in this library against the policy now" onClick={() => evaluate.mutate(undefined, { onSuccess: (r) => toast(r.jobs_created ? `Queued ${r.jobs_created} file(s)` : "No new files to queue") })}>
              Apply now
            </button>
          )}
          <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate(policy, { onSuccess: () => toast("Aging policy saved. It applies on the next scan, or click Apply now."), onError: (e) => toast((e as Error).message, "err") })}>
            Save policy
          </button>
        </div>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Rule builder
// ---------------------------------------------------------------------------

const EMPTY_GROUP: ConditionGroup = { type: "group", op: "all", children: [{ type: "condition", field: "file_age_days", operator: "gt", value: 90 }] };

function RuleEditor({ rule, onClose }: { rule: Rule | null; onClose: () => void }) {
  const toast = useToast();
  const { data: meta } = useRuleFields();
  const { data: profiles } = useProfiles();
  const { data: libraries } = useLibraries();
  const [name, setName] = useState(rule?.name ?? "");
  const [libraryId, setLibraryId] = useState<number | null>(rule?.library_id ?? null);
  const [conditions, setConditions] = useState<ConditionGroup>(rule?.conditions ?? EMPTY_GROUP);
  const [action, setAction] = useState<"transcode" | "skip">(rule?.action ?? "transcode");
  const [profileId, setProfileId] = useState<number | null>(rule?.profile_id ?? null);
  const [priority, setPriority] = useState(rule?.priority ?? 2);
  const [windowOn, setWindowOn] = useState(!!rule?.schedule.window);
  const [win, setWin] = useState(rule?.schedule.window ?? { start: "23:00", end: "07:00", days: [0, 1, 2, 3, 4, 5, 6] });
  const [skipTarget, setSkipTarget] = useState(rule?.skip_if_target_codec ?? true);
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [preview, setPreview] = useState<{ count: number; total_files: number; total_bytes: number; sample: { id: number; filename: string; size: number }[] } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chosenProfile = profileId ?? profiles?.[0]?.id ?? null;

  const conditionsKey = JSON.stringify(conditions);
  useEffect(() => {
    const t = window.setTimeout(() => {
      api
        .post<typeof preview>("/rules/preview", { conditions, library_id: libraryId, limit: 8 })
        .then((p) => {
          setPreview(p);
          setPreviewError(null);
        })
        .catch((e: Error) => setPreviewError(e.message));
    }, 350);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conditionsKey, libraryId]);

  const save = useAction(() => {
    const body = {
      name,
      description: rule?.description ?? "",
      library_id: libraryId,
      enabled,
      conditions,
      action,
      profile_id: action === "transcode" ? chosenProfile : null,
      priority,
      schedule: windowOn ? { window: win } : {},
      skip_if_target_codec: skipTarget,
    };
    return rule ? api.put(`/rules/${rule.id}`, body) : api.post("/rules", body);
  }, [["rules"]]);

  if (!meta) return null;
  return (
    <Modal
      wide
      title={rule ? "Edit rule" : "New rule"}
      subtitle={rule?.policy_group ? "Editing this rule detaches it from the aging policy." : undefined}
      onClose={onClose}
      footer={
        <>
          <Toggle checked={enabled} onChange={setEnabled} label="Enabled" />
          <span className="spacer" />
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={!name || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: () => { toast("Rule saved"); onClose(); }, onError: (e) => setError((e as Error).message) })}>
            Save rule
          </button>
        </>
      }
    >
      <div className="stack gap-6">
        <div className="form-grid">
          <Field label="Name">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Archive old 1440p+ gameplay" autoFocus={!rule} />
          </Field>
          <Field label="Applies to">
            <select className="select" value={libraryId ?? ""} onChange={(e) => setLibraryId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">All libraries</option>
              {libraries?.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="sub-section">
          <div className="when-then">When</div>
          <GroupEditor group={conditions} onChange={setConditions} fields={meta.fields} depth={0} />
          <div className="small match-preview">
            {previewError ? (
              <span className="text-err">{previewError}</span>
            ) : preview ? (
              <span className="muted">
                Matches <b className="strong">{preview.count}</b> of {preview.total_files} files right now ({bytes(preview.total_bytes)})
                {preview.sample.length > 0 && <span className="faint"> · e.g. {preview.sample.slice(0, 3).map((s) => s.filename).join(", ")}</span>}
              </span>
            ) : null}
          </div>
        </div>

        <div className="sub-section">
          <div className="when-then">Then</div>
          <div className="stack gap-4">
            <div className="row wrap">
              <Segmented
                value={action}
                onChange={setAction}
                options={[
                  { value: "transcode", label: "Convert using" },
                  { value: "skip", label: "Leave it alone" },
                ]}
              />
              {action === "transcode" && (
                <>
                  <select className="select w-select" aria-label="Profile" value={chosenProfile ?? ""} onChange={(e) => setProfileId(Number(e.target.value))}>
                    {profiles?.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <span className="muted small">at</span>
                  <select className="select w-select-sm" aria-label="Priority" value={priority} onChange={(e) => setPriority(Number(e.target.value))}>
                    {PRIORITIES.map((p, i) => (
                      <option key={p} value={i}>
                        {p}
                      </option>
                    ))}
                  </select>
                  <span className="muted small">priority</span>
                </>
              )}
            </div>
            {action === "transcode" && (
              <div className="settings-list">
                <SettingRow label="Skip files already in the target codec" description="Unless the profile also downsizes them.">
                  <Toggle checked={skipTarget} onChange={setSkipTarget} />
                </SettingRow>
                <SettingRow label="Only run these jobs between" dim={!windowOn}>
                  <Toggle checked={windowOn} onChange={setWindowOn} />
                  <input className="input mono w-time" type="time" aria-label="Start" disabled={!windowOn} value={win.start} onChange={(e) => setWin({ ...win, start: e.target.value })} />
                  <span className="faint">and</span>
                  <input className="input mono w-time" type="time" aria-label="End" disabled={!windowOn} value={win.end} onChange={(e) => setWin({ ...win, end: e.target.value })} />
                </SettingRow>
              </div>
            )}
          </div>
        </div>
        {error && <Callout kind="err" title="Couldn't save">{error}</Callout>}
      </div>
    </Modal>
  );
}

function GroupEditor({ group, onChange, fields, depth }: { group: ConditionGroup; onChange: (g: ConditionGroup) => void; fields: FieldDef[]; depth: number }) {
  const setChild = (i: number, c: ConditionGroup | ConditionLeaf) => onChange({ ...group, children: group.children.map((x, j) => (j === i ? c : x)) });
  const remove = (i: number) => onChange({ ...group, children: group.children.filter((_, j) => j !== i) });
  const joinWord = group.op === "any" ? "OR" : group.op === "none" ? "NOR" : "AND";

  return (
    <div className="rule-group">
      <div className="row small wrap">
        <span className="muted">Match</span>
        <Segmented
          value={group.op}
          onChange={(op) => onChange({ ...group, op })}
          options={[
            { value: "all", label: "all" },
            { value: "any", label: "any" },
            { value: "none", label: "none" },
          ]}
        />
        <span className="muted">of these</span>
      </div>
      {group.children.map((c, i) =>
        c.type === "group" ? (
          <div key={i} className="cond-nested">
            <span className="cond-join">{i === 0 ? "" : joinWord}</span>
            <div className="flex-1">
              <GroupEditor group={c} onChange={(g) => setChild(i, g)} fields={fields} depth={depth + 1} />
            </div>
            <button className="btn icon ghost" onClick={() => remove(i)} title="Remove group" aria-label="Remove group">
              <X size={16} />
            </button>
          </div>
        ) : (
          <ConditionRow key={i} join={i === 0 ? "" : joinWord} cond={c} fields={fields} onChange={(x) => setChild(i, x)} onRemove={() => remove(i)} />
        ),
      )}
      <div className="row cond-add">
        <button className="btn sm ghost" onClick={() => onChange({ ...group, children: [...group.children, { type: "condition", field: "video_codec", operator: "is", value: "h264" }] })}>
          <Plus size={14} /> Condition
        </button>
        {depth < 2 && (
          <button className="btn sm ghost" onClick={() => onChange({ ...group, children: [...group.children, { type: "group", op: "any", children: [] }] })}>
            <Plus size={14} /> Group
          </button>
        )}
      </div>
    </div>
  );
}

function defaultValue(f: FieldDef, op: string): unknown {
  if (f.kind === "bool") return null;
  if (f.kind === "time") return ["23:00", "07:00"];
  if (f.kind === "day") return [5, 6];
  if (op === "between") return [0, 100];
  if (op === "in" || op === "not_in") return f.options?.slice(0, 1).map((o) => o.value) ?? [];
  if (f.options?.length) return f.options[0].value;
  if (f.kind === "number") return 0;
  return "";
}

export function ConditionRow({ cond, fields, onChange, onRemove, join }: { cond: ConditionLeaf; fields: FieldDef[]; onChange: (c: ConditionLeaf) => void; onRemove: () => void; join: string }) {
  const f = fields.find((x) => x.key === cond.field) ?? fields[0];
  const groups = useMemo(() => [...new Set(fields.map((x) => x.group))], [fields]);

  const setField = (key: string) => {
    const nf = fields.find((x) => x.key === key)!;
    const op = nf.operators[0].key;
    onChange({ type: "condition", field: key, operator: op, value: defaultValue(nf, op) });
  };
  const setOp = (op: string) => {
    const shapeChanged = (op === "between") !== (cond.operator === "between") || ["in", "not_in"].includes(op) !== ["in", "not_in"].includes(cond.operator);
    onChange({ ...cond, operator: op, value: shapeChanged ? defaultValue(f, op) : cond.value });
  };

  return (
    <div className="cond-row">
      <span className="cond-join">{join}</span>
      <select className="select" value={cond.field} onChange={(e) => setField(e.target.value)} title={f.help ?? undefined}>
        {groups.map((g) => (
          <optgroup key={g} label={g}>
            {fields
              .filter((x) => x.group === g)
              .map((x) => (
                <option key={x.key} value={x.key}>
                  {x.label}
                </option>
              ))}
          </optgroup>
        ))}
      </select>
      <select className="select" value={cond.operator} onChange={(e) => setOp(e.target.value)}>
        {f.operators.map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
      </select>
      <ValueEditor field={f} op={cond.operator} value={cond.value} onChange={(v) => onChange({ ...cond, value: v })} />
      <button className="btn icon ghost" onClick={onRemove} aria-label="Remove condition" title="Remove condition">
        <X size={16} />
      </button>
    </div>
  );
}

function ValueEditor({ field, op, value, onChange }: { field: FieldDef; op: string; value: unknown; onChange: (v: unknown) => void }) {
  const unit = field.unit && field.unit !== "p" ? field.unit : undefined;
  if (field.kind === "bool") return <span />;
  if (field.kind === "time") {
    const [a, b] = (Array.isArray(value) ? value : ["23:00", "07:00"]) as string[];
    return (
      <div className="row">
        <input className="input mono" type="time" value={a} onChange={(e) => onChange([e.target.value, b])} />
        <input className="input mono" type="time" value={b} onChange={(e) => onChange([a, e.target.value])} />
      </div>
    );
  }
  if (field.kind === "day" || op === "in" || op === "not_in") {
    const list = (Array.isArray(value) ? value : [value]).filter((x) => x !== "" && x != null);
    return (
      <div className="row wrap gap-1">
        {(field.options ?? []).map((o) => {
          const on = list.includes(o.value);
          return (
            <button key={String(o.value)} type="button" aria-pressed={on} className={`chip ${on ? "accent" : "outline"}`} onClick={() => onChange(on ? list.filter((x) => x !== o.value) : [...list, o.value])}>
              {o.label}
            </button>
          );
        })}
      </div>
    );
  }
  if (op === "between") {
    const [a, b] = (Array.isArray(value) ? value : [0, 0]) as number[];
    return (
      <div className="row">
        <NumberInput value={a} onChange={(v) => onChange([v ?? 0, b])} />
        <NumberInput value={b} onChange={(v) => onChange([a, v ?? 0])} suffix={unit} />
      </div>
    );
  }
  if (field.options?.length && (field.kind === "enum" || field.key === "resolution")) {
    const known = field.options.some((o) => o.value === value);
    return (
      <select className="select" value={known ? String(value) : "__custom"} onChange={(e) => e.target.value !== "__custom" && onChange(field.kind === "number" ? Number(e.target.value) : e.target.value)}>
        {field.options.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label}
          </option>
        ))}
        {!known && <option value="__custom">{String(value)}</option>}
      </select>
    );
  }
  if (field.kind === "number") return <NumberInput value={typeof value === "number" ? value : Number(value) || 0} onChange={(v) => onChange(v ?? 0)} suffix={unit} step={field.unit === "GB" ? 0.5 : 1} />;
  return <input className="input mono" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} placeholder={op === "glob" ? "*/raw/*" : op === "matches" ? "^VOD_\\d+" : ""} />;
}
