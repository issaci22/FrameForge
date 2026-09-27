import type { AudioCodec, CompatIssue, ProfileOptions, ProfileSpec } from "../../api/types";
import { NumberInput, Segmented, Toggle } from "../ui";
import { CompatIssues, type OnSpec } from "./parts";

const FALLBACK_CODECS: { value: AudioCodec; label: string }[] = [
  { value: "aac", label: "AAC" },
  { value: "opus", label: "Opus" },
  { value: "ac3", label: "AC-3" },
  { value: "eac3", label: "E-AC-3" },
  { value: "flac", label: "FLAC" },
];

/** Keep or convert audio. The codec and bitrate only appear when a track would actually be converted. */
export function AudioSection({ spec, onChange, options, issues }: { spec: ProfileSpec; onChange: OnSpec; options?: ProfileOptions; issues?: CompatIssue[] }) {
  const keep = spec.audio_mode !== "transcode";
  const info = options?.audio_codecs.find((a) => a.value === spec.audio_codec);
  const codecs = options?.audio_codecs.map((a) => ({ value: a.value, label: a.label.split(" (")[0] })) ?? FALLBACK_CODECS;
  const pickCodec = (v: AudioCodec) => {
    const next = options?.audio_codecs.find((a) => a.value === v);
    onChange({ audio_codec: v, ...(next?.default_kbps ? { audio_bitrate_kbps: next.default_kbps } : {}) });
  };
  return (
    <div className="stack gap-3">
      <div className="tx-row">
        <span className="tx-label">Audio tracks</span>
        <Segmented
          value={keep ? "keep" : "convert"}
          onChange={(v) => onChange({ audio_mode: v === "keep" ? "copy_compatible" : "transcode" })}
          options={[
            { value: "keep", label: "Keep original when possible" },
            { value: "convert", label: "Convert every track" },
          ]}
        />
      </div>
      <div className="faint small">
        {keep
          ? "Tracks are copied untouched (no quality loss). Only tracks the container can't hold are converted. Every track is kept: game, mic and chat audio from OBS all survive."
          : "Every track is re-encoded. Useful to shrink high-bitrate or uncompressed audio, or to get one audio format everywhere."}
      </div>
      {keep && spec.container === "mp4" && (
        <Toggle
          checked={spec.audio_copy_scope === "widely_playable"}
          onChange={(v) => onChange({ audio_copy_scope: v ? "widely_playable" : "storable" })}
          label="Also convert tracks that play poorly in MP4 (Opus, FLAC) instead of copying them"
        />
      )}
      <div className="tx-row">
        <span className="tx-label">{keep ? "Convert to" : "Codec"}</span>
        <Segmented value={spec.audio_codec} onChange={pickCodec} options={codecs} />
        {spec.audio_codec !== "flac" && (
          <div className="w-wide-num">
            <NumberInput value={spec.audio_bitrate_kbps} onChange={(v) => onChange({ audio_bitrate_kbps: v ?? info?.default_kbps ?? 160 })} min={32} max={1024} suffix="kb/s per stereo pair" />
          </div>
        )}
      </div>
      {info && <div className="faint small">{info.description}</div>}
      <CompatIssues issues={issues} fields={["audio_codec", "audio_bitrate_kbps", "audio_copy_scope"]} onApply={onChange} />
    </div>
  );
}
