import type { KeyboardEvent } from "react";
import { RotateCcw } from "lucide-react";
import { useProfileOptions } from "../../api/hooks";
import type { Profile, ProfileSpec } from "../../api/types";
import { CodecChip } from "../../components/media";
import { AdvancedSection } from "../../components/transcode/AdvancedSection";
import { AudioSection } from "../../components/transcode/AudioSection";
import { FormatSection } from "../../components/transcode/FormatSection";
import { QualityControl } from "../../components/transcode/QualityControl";
import { Callout } from "../../components/ui";
import { useProfilePreview } from "./hooks";
import { effectiveSpec, specChanged } from "./model";
import { GuideSection, IssueNote, Panel, StepFrame, type StepProps } from "./parts";

export function TranscodingStep({ draft, update, server, issues, showErrors }: StepProps) {
  const profiles = server.profiles;
  const selected = profiles?.find((x) => x.id === draft.archiveProfileId) ?? null;
  const spec = selected ? effectiveSpec(draft, selected) : null;
  const preview = useProfilePreview(spec, !!spec);
  const { data: options } = useProfileOptions();
  const compat = preview.data?.issues;
  const edited = !!selected && !!draft.profileEdits[selected.id];

  const setSpec = (patch: Partial<ProfileSpec>) => {
    if (!selected) return;
    update((d) => {
      const next = { ...effectiveSpec(d, selected), ...patch };
      const edits = { ...d.profileEdits };
      if (specChanged(next, selected.spec)) edits[selected.id] = next;
      else delete edits[selected.id];
      return { ...d, profileEdits: edits };
    });
  };
  const reset = () =>
    selected &&
    update((d) => {
      const edits = { ...d.profileEdits };
      delete edits[selected.id];
      return { ...d, profileEdits: edits };
    });
  const choose = (id: number) =>
    update((d) => ({
      ...d,
      archiveProfileId: id,
      // Aging stages that followed the previous archive profile follow the new one.
      aging: { ...d.aging, stages: d.aging.stages.map((s) => (s.action === "transcode" && s.profile_id === d.archiveProfileId ? { ...s, profile_id: id } : s)) },
    }));

  return (
    <StepFrame
      step="transcoding"
      title="Choose how footage is converted"
      lede="The archive profile describes the result: codec, container, quality and what to keep. It doesn't name an encoder. Each node picks the best one it has verified, so the same profile works on a CPU-only NAS and on a GPU desktop."
      guide={<TranscodingGuide />}
    >
      <Panel title="Archive profile" description="Used by the aging policy's conversion stage. Pick a starting point; you can fine-tune it below." right={edited && selected ? <ChangesNote name={selected.name} onReset={reset} /> : undefined} tight>
        {!profiles ? (
          <div className="panel-body muted small">Loading profiles…</div>
        ) : profiles.length === 0 ? (
          <div className="panel-body muted small">No profiles exist on this server. Create one on the Profiles page after setup.</div>
        ) : (
          <ProfilePicker profiles={profiles} value={draft.archiveProfileId} edited={draft.profileEdits} onChange={choose} />
        )}
      </Panel>

      {selected && spec && (
        <>
          {spec.video_codec === "copy" ? (
            <Callout kind="info" title={`${selected.name} copies the video stream`}>
              Remux profiles change only the container, so there is no quality setting or encoder to choose. The audio and stream options below still apply.
            </Callout>
          ) : (
            <Panel title="Quality" description="Smaller files on the left, better quality on the right. The native value each encoder uses is shown too.">
              {preview.error && <Callout kind="err" title="Couldn't translate this profile">{(preview.error as Error).message}</Callout>}
              <QualityControl spec={spec} onChange={setSpec} options={options} preview={preview.data} history={selected.history} />
            </Panel>
          )}
          <Panel title="Format">
            <FormatSection spec={spec} onChange={setSpec} options={options} issues={compat} />
            <IssueNote issues={issues} field="hardware" showErrors />
          </Panel>
          <Panel title="Audio">
            <AudioSection spec={spec} onChange={setSpec} options={options} issues={compat} />
            <IssueNote issues={issues} field="audio" showErrors={showErrors} issueKey={String(selected.id)} />
          </Panel>
          <AdvancedSection spec={spec} onChange={setSpec} options={options} issues={compat}>
            <div className="faint small">Raw FFmpeg arguments and the exact command each backend runs are on the Profiles page.</div>
          </AdvancedSection>
        </>
      )}
    </StepFrame>
  );
}

const DIFF_LABELS: Partial<Record<keyof ProfileSpec, string>> = {
  container: "container",
  video_codec: "codec",
  quality: "quality",
  max_resolution: "max resolution",
  max_fps: "max fps",
  speed: "speed",
  hw_mode: "hardware",
  allow_cpu_fallback: "CPU fallback",
  hw_decode: "hardware decoding",
  ten_bit: "10-bit",
  audio_mode: "audio",
  audio_codec: "audio codec",
  audio_bitrate_kbps: "audio kb/s",
  audio_copy_scope: "audio copy",
  keep_metadata: "metadata",
  keep_chapters: "chapters",
  keep_subtitles: "subtitles",
  keep_attachments: "attachments",
  faststart: "faststart",
};

/** The settings the wizard changed, e.g. "quality 72 → 60 · speed balanced → max". */
function specDiff(from: ProfileSpec, to: ProfileSpec): string {
  const fmt = (v: unknown) => (typeof v === "boolean" ? (v ? "on" : "off") : String(v));
  return (Object.keys(DIFF_LABELS) as (keyof ProfileSpec)[])
    .filter((k) => from[k] !== to[k])
    .map((k) => `${DIFF_LABELS[k]} ${fmt(from[k])} → ${fmt(to[k])}`)
    .join(" · ");
}

function ProfilePicker({ profiles, value, edited, onChange }: { profiles: Profile[]; value: number | null; edited: Record<number, ProfileSpec>; onChange: (id: number) => void }) {
  const idx = profiles.findIndex((x) => x.id === value);
  // Arrow keys move the selection, like native radio buttons.
  const onKey = (e: KeyboardEvent) => {
    const delta = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    const next = profiles[(Math.max(idx, 0) + delta + profiles.length) % profiles.length];
    onChange(next.id);
    window.requestAnimationFrame(() => document.getElementById(`profile-${next.id}`)?.focus());
  };
  return (
    <div className="pick-list" role="radiogroup" aria-label="Archive profile" onKeyDown={onKey}>
      {profiles.map((pr, i) => {
        const on = pr.id === value;
        const spec = edited[pr.id] ?? pr.spec;
        return (
          <button
            key={pr.id}
            id={`profile-${pr.id}`}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on || (idx < 0 && i === 0) ? 0 : -1}
            className={`pick-row ${on ? "on" : ""}`}
            onClick={() => onChange(pr.id)}
          >
            <span className="radio" aria-hidden />
            <span className="pick-main">
              <span className="pick-name">
                {pr.name}
                {pr.builtin && <span className="chip outline">Built-in</span>}
                {edited[pr.id] && <span className="chip accent">Modified</span>}
              </span>
              <span className="pick-desc">{pr.description}</span>
            </span>
            <span className="row gap-2">
              <CodecChip codec={spec.video_codec} />
              <span className="mono small faint">{spec.container.toUpperCase()}</span>
            </span>
            {edited[pr.id] ? <span className="pick-summary changed small">Changed: {specDiff(pr.spec, edited[pr.id])}</span> : <span className="pick-summary small">{pr.summary}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Edits are saved into the selected profile at the end; this says so and offers a way back. */
function ChangesNote({ name, onReset }: { name: string; onReset: () => void }) {
  return (
    <>
      <span className="faint small">Changes are saved to {name} when you complete setup</span>
      <button type="button" className="btn sm" onClick={onReset}>
        <RotateCcw size={12} /> Discard changes
      </button>
    </>
  );
}

function TranscodingGuide() {
  return (
    <>
      <GuideSection title="Where changes go">
        <div>Changes here are saved to the selected profile when you complete setup. Other profiles stay as they are, and every profile can be edited later on the Profiles page.</div>
      </GuideSection>
      <GuideSection title="Codecs">
        <dl className="kv narrow">
          <dt>H.264</dt>
          <dd>Plays everywhere. Largest files.</dd>
          <dt>H.265</dt>
          <dd>About half the size of H.264 at similar quality.</dd>
          <dt>AV1</dt>
          <dd>Smallest files. Slow on CPU; hardware AV1 needs a recent GPU.</dd>
        </dl>
      </GuideSection>
      <GuideSection title="Software encoders">
        <div>
          <span className="mono">libx264</span>, <span className="mono">libx265</span> and <span className="mono">libsvtav1</span> run on any node and give the best quality per megabyte. GPU encoders (NVENC, Quick Sync, VA-API) are much faster; VA-API runs in constant-QP mode and often produces larger files at the same number.
        </div>
      </GuideSection>
    </>
  );
}
