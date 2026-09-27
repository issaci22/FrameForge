import { useMemo, useState } from "react";
import { RotateCcw, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { useAction, useProfileOptions, useProfilePreview } from "../../api/hooks";
import type { Profile, ProfileSpec, StartingPoint } from "../../api/types";
import { AdvancedSection } from "../../components/transcode/AdvancedSection";
import { AudioSection } from "../../components/transcode/AudioSection";
import { CompressionPreviewButton } from "../../components/transcode/CompressionPreview";
import { FormatSection } from "../../components/transcode/FormatSection";
import { CompatIssues, TxSection } from "../../components/transcode/parts";
import { QualityControl } from "../../components/transcode/QualityControl";
import { StartingPoints } from "../../components/transcode/StartingPoints";
import { Callout, ConfirmButton, Field, Modal, Tabs, useToast } from "../../components/ui";
import { DEFAULT_SPEC, specSummary, sameSpec } from "./spec";

/**
 * The profile editor, organized the way people think about an output:
 * goal (starting point) → quality → format → audio, with everything technical under Advanced.
 */
export function ProfileEditor({ profile, base, onClose }: { profile: Profile | null; base: Profile | null; onClose: () => void }) {
  const toast = useToast();
  const { data: options } = useProfileOptions();
  const seed = profile ?? base;
  const [name, setName] = useState(profile ? profile.name : base ? `${base.name} (copy)` : "");
  const [description, setDescription] = useState(seed?.description ?? "");
  const [basedOn, setBasedOn] = useState<string | null>(seed ? seed.based_on ?? seed.builtin_key : null);
  const [spec, setSpec] = useState<ProfileSpec>({ ...DEFAULT_SPEC, ...(seed?.spec ?? {}) });
  const [extraArgs, setExtraArgs] = useState((seed?.spec.extra_video_args ?? []).join(" "));
  const [error, setError] = useState<string | null>(null);
  const [previewBackend, setPreviewBackend] = useState("cpu");
  const choosing = !seed; // a brand-new profile starts by picking a goal
  const set = (patch: Partial<ProfileSpec>) => setSpec((s) => ({ ...s, ...patch }));

  const fullSpec = useMemo(() => ({ ...spec, extra_video_args: extraArgs.trim() ? extraArgs.trim().split(/\s+/) : [] }), [spec, extraArgs]);
  const { data: preview, error: previewError } = useProfilePreview(fullSpec);
  const issues = preview?.issues ?? [];
  const blocking = issues.filter((i) => i.level === "error");
  const remux = spec.video_codec === "copy";

  const start = options?.starting_points.find((p) => p.key === basedOn) ?? null;
  const modified = !!start && !sameSpec(start.spec, fullSpec);

  const pickStart = (p: StartingPoint) => {
    const prev = options?.starting_points.find((x) => x.key === basedOn);
    setSpec({ ...DEFAULT_SPEC, ...p.spec });
    setExtraArgs("");
    setBasedOn(p.key);
    if (!name.trim() || (prev && name === `My ${prev.name}`)) setName(`My ${p.name}`);
    if (!description.trim() || (prev && description === prev.description)) setDescription(p.description);
  };

  const save = useAction(
    () => {
      const body = { name, description, based_on: basedOn, spec: fullSpec };
      return profile ? api.put(`/profiles/${profile.id}`, body) : api.post("/profiles", body);
    },
    [["profiles"]],
  );
  const del = useAction(() => api.del(`/profiles/${profile!.id}`), [["profiles"]]);
  const cmd = preview?.commands.find((c) => c.backend === previewBackend) ?? preview?.commands[0];

  const subtitle = profile?.builtin_group === "legacy"
    ? "Built-in profile from an earlier FrameForge version. Kept because you changed it."
    : profile?.builtin
      ? "Built-in profile. You can change it; new versions of FrameForge won't overwrite your edits."
      : undefined;

  return (
    <Modal
      wide
      title={profile ? profile.name : "New profile"}
      subtitle={subtitle}
      onClose={onClose}
      footer={
        <>
          {profile && (
            <ConfirmButton
              className="btn danger"
              confirmText={`Delete “${profile.name}”?`}
              detail="Profiles still used by a rule or a queued job can't be deleted."
              confirmLabel="Delete"
              onConfirm={() => del.mutate(undefined, { onSuccess: onClose, onError: (e) => toast((e as Error).message, "err") })}
            >
              <Trash2 size={14} /> Delete
            </ConfirmButton>
          )}
          <span className="spacer" />
          <span className="tx-summary mono small" title="What this profile produces">
            {specSummary(fullSpec, options)}
          </span>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn primary"
            disabled={!name.trim() || save.isPending || blocking.length > 0}
            title={blocking.length ? blocking.map((i) => i.message).join(" ") : undefined}
            onClick={() => save.mutate(undefined, { onSuccess: () => { toast("Profile saved"); onClose(); }, onError: (e) => setError((e as Error).message) })}
          >
            Save profile
          </button>
        </>
      }
    >
      <div className="stack tx-editor">
        {options?.starting_points && (choosing || !profile) && (
          <TxSection title="Goal" hint="Pick the closest starting point. You can change anything afterwards.">
            <StartingPoints points={options.starting_points} value={basedOn} onPick={pickStart} />
          </TxSection>
        )}

        <div className="form-grid">
          <Field label="Name">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus={!!profile} />
          </Field>
          <Field label="Description">
            <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
        {start && (
          <div className="row small gap-2">
            <span className="chip outline">
              Based on {start.name}
              {modified ? " · customized" : ""}
            </span>
            {modified && (
              <ConfirmButton
                className="btn sm ghost"
                confirmText={`Reset every setting to ${start.name}?`}
                detail="Your changes to quality, format, audio and advanced settings are discarded."
                confirmLabel="Reset"
                onConfirm={() => {
                  setSpec({ ...DEFAULT_SPEC, ...start.spec });
                  setExtraArgs("");
                }}
              >
                <RotateCcw size={14} /> Reset to {start.name}
              </ConfirmButton>
            )}
          </div>
        )}

        {!remux && (
          <TxSection title="Quality" hint="Smaller file ←→ better quality">
            <QualityControl spec={spec} onChange={set} options={options} preview={preview} history={profile?.history} extra={<CompressionPreviewButton spec={fullSpec} availability={preview?.compression_preview} />} />
          </TxSection>
        )}

        <TxSection title="Format" hint="Container and video codec are separate choices">
          <FormatSection spec={spec} onChange={set} options={options} issues={issues} />
        </TxSection>

        <TxSection title="Audio">
          <AudioSection spec={spec} onChange={set} options={options} issues={issues} />
        </TxSection>

        <AdvancedSection spec={spec} onChange={set} options={options} issues={issues} extraArgs={extraArgs} onExtraArgs={setExtraArgs}>
          {preview && preview.commands.length > 0 && (
            <div className="tx-group">
              <div className="sec-label">
                What FFmpeg will run <span className="faint sec-label-note">{preview.sample}</span>
              </div>
              {!remux && <Tabs value={previewBackend} onChange={setPreviewBackend} tabs={preview.commands.map((c) => ({ value: c.backend, label: c.label ?? c.backend }))} />}
              {cmd?.pipeline && <div className="small muted">{cmd.pipeline.join("  →  ")}</div>}
              {cmd?.command && <pre className="log short">{cmd.command}</pre>}
              {cmd?.notes?.map((n) => (
                <Callout key={n} kind="warn">
                  {n}
                </Callout>
              ))}
              {preview.nodes && preview.nodes.length > 0 && (
                <div className="small">
                  {preview.nodes.map((n) => (
                    <div key={n.node_id}>
                      <b>{n.name}</b>: {n.encoder ? <span className="mono">would use {n.encoder}{n.native ? ` (${n.native})` : ""}</span> : <span className="text-err">{n.reason}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </AdvancedSection>

        <CompatIssues issues={blocking} onApply={set} />
        {previewError && <Callout kind="err" title="Invalid setting">{(previewError as Error).message}</Callout>}
        {error && <Callout kind="err" title="Couldn't save">{error}</Callout>}
      </div>
    </Modal>
  );
}
