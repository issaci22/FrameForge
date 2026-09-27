import type { ReactNode } from "react";
import type { CompatIssue, ProfileOptions, ProfileSpec } from "../../api/types";
import { Field, NumberInput, Segmented, Toggle } from "../ui";
import { CompatIssues, hardwareFor, type OnSpec } from "./parts";

const HW: { value: ProfileSpec["hw_mode"]; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "cpu", label: "CPU only" },
  { value: "nvenc", label: "NVIDIA" },
  { value: "qsv", label: "Intel QSV" },
  { value: "vaapi", label: "VA-API" },
];

/**
 * Settings most people never need, grouped so they're findable. Collapsed by default.
 * ``extraArgs`` is optional so the setup wizard can leave raw FFmpeg arguments to the Profiles page.
 */
export function AdvancedSection({
  spec,
  onChange,
  options,
  issues,
  extraArgs,
  onExtraArgs,
  open,
  children,
}: {
  spec: ProfileSpec;
  onChange: OnSpec;
  options?: ProfileOptions;
  issues?: CompatIssue[];
  extraArgs?: string;
  onExtraArgs?: (v: string) => void;
  open?: boolean;
  children?: ReactNode;
}) {
  const remux = spec.video_codec === "copy";
  const byBackend = options?.hardware[spec.video_codec] ?? {};
  const hwOptions = HW.map((o) => {
    if (o.value === "auto" || o.value === "cpu") return o;
    const nodes = byBackend[o.value] ?? [];
    const known = !!options && options.online_nodes > 0;
    return {
      value: o.value,
      label: (
        <span className="row gap-1">
          <span className={`hw-dot ${nodes.length ? "available" : ""}`} aria-hidden />
          {o.label}
        </span>
      ),
      title: nodes.length ? `Verified on ${nodes.join(", ")}` : known ? "No online node verified this encoder" : "No node online",
    };
  });
  const hw = hardwareFor(options, spec.video_codec);
  return (
    <details className="disclosure tx-advanced" open={open}>
      <summary>Advanced</summary>
      <div className="stack gap-4 mt-3">
        {!remux && (
          <div className="tx-group">
            <div className="sec-label">Hardware</div>
            <div className="tx-row">
              <span className="tx-label">Encoder</span>
              <Segmented value={spec.hw_mode} onChange={(v) => onChange({ hw_mode: v })} options={hwOptions} />
            </div>
            <div className="faint small">
              {spec.hw_mode === "auto" ? `Auto picks the first verified encoder: NVENC → Quick Sync → VA-API → AMF, then the CPU. ${hw.detail}` : "Pinned to one encoder family. Nodes without it can only help through CPU fallback."}
            </div>
            <div className="toggle-grid">
              <Toggle checked={spec.allow_cpu_fallback || spec.hw_mode === "cpu"} disabled={spec.hw_mode === "cpu"} onChange={(v) => onChange({ allow_cpu_fallback: v })} label="Fall back to the CPU when a node has no verified GPU encoder" />
              <Toggle checked={spec.hw_decode} onChange={(v) => onChange({ hw_decode: v })} label="Hardware decoding when verified (falls back to software automatically)" />
              <Toggle checked={spec.ten_bit === "auto"} onChange={(v) => onChange({ ten_bit: v ? "auto" : "never" })} label="Keep 10-bit / HDR when the source has it" />
            </div>
            <CompatIssues issues={issues} fields={["hw_mode"]} onApply={onChange} />
          </div>
        )}

        {!remux && (
          <div className="tx-group">
            <div className="sec-label">Rate control</div>
            <Segmented
              value={spec.rate_control}
              onChange={(v) => onChange({ rate_control: v })}
              options={[
                { value: "quality", label: "Quality slider" },
                { value: "constant_quality", label: "Exact CRF / CQ / QP" },
                { value: "bitrate", label: "Target bitrate" },
              ]}
            />
            <div className="form-grid">
              {spec.rate_control === "constant_quality" && (
                <Field label="Encoder value" help="Applied as-is to whichever encoder runs, clamped to its range. Lower = better.">
                  <NumberInput value={spec.constant_quality} onChange={(v) => onChange({ constant_quality: v })} min={0} max={255} allowEmpty />
                </Field>
              )}
              {spec.rate_control === "bitrate" && (
                <Field label="Target bitrate" help="Predictable size, less predictable quality. Peaks up to 1.5×.">
                  <NumberInput value={spec.bitrate_kbps} onChange={(v) => onChange({ bitrate_kbps: v })} min={100} suffix="kb/s" allowEmpty />
                </Field>
              )}
              <Field label="Encoder preset override" help="Raw preset name, e.g. p7 or slow. Empty uses Encoding effort.">
                <input className="input mono" value={spec.encoder_preset ?? ""} onChange={(e) => onChange({ encoder_preset: e.target.value || null })} />
              </Field>
              {onExtraArgs && (
                <Field label="Extra video arguments" help="Appended to the encoder options. Inputs and outputs are managed by FrameForge.">
                  <input className="input mono" value={extraArgs ?? ""} onChange={(e) => onExtraArgs(e.target.value)} placeholder="-g 240" />
                </Field>
              )}
            </div>
            <CompatIssues issues={issues} fields={["bitrate_kbps", "constant_quality"]} onApply={onChange} />
          </div>
        )}

        <div className="tx-group">
          <div className="sec-label">Streams & metadata</div>
          <div className="toggle-grid">
            <Toggle checked={spec.keep_subtitles} onChange={(v) => onChange({ keep_subtitles: v })} label="Keep subtitles" />
            <Toggle checked={spec.keep_chapters} onChange={(v) => onChange({ keep_chapters: v })} label="Keep chapters" />
            <Toggle checked={spec.keep_metadata} onChange={(v) => onChange({ keep_metadata: v })} label="Keep metadata" />
            <Toggle checked={spec.keep_attachments} onChange={(v) => onChange({ keep_attachments: v })} label="Keep attachments (fonts, cover art; MKV only)" />
            {spec.container === "mp4" && <Toggle checked={spec.faststart} onChange={(v) => onChange({ faststart: v })} label="Web-optimized MP4 (faststart)" />}
          </div>
          <div className="faint small">Color and HDR tags are always carried over. Anything dropped or converted is listed on the job.</div>
        </div>

        <div className="tx-group">
          <div className="sec-label">Engine</div>
          <Segmented
            value={spec.engine}
            onChange={(v) => onChange({ engine: v })}
            options={[
              { value: "ffmpeg", label: "FFmpeg" },
              { value: "handbrake", label: "HandBrake (unavailable)", disabled: true, title: "Not included in this version" },
            ]}
          />
        </div>
        {children}
      </div>
    </details>
  );
}
