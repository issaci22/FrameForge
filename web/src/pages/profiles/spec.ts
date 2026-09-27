import type { ProfileOptions, ProfileSpec } from "../../api/types";
import { codec } from "../../lib/format";

export const DEFAULT_SPEC: ProfileSpec = {
  version: 1,
  engine: "ffmpeg",
  container: "mp4",
  video_codec: "hevc",
  quality: 68,
  speed: "balanced",
  max_resolution: null,
  max_fps: null,
  hw_mode: "auto",
  allow_cpu_fallback: true,
  hw_decode: true,
  ten_bit: "auto",
  audio_mode: "copy_compatible",
  audio_codec: "aac",
  audio_bitrate_kbps: 160,
  audio_copy_scope: "storable",
  keep_subtitles: true,
  keep_chapters: true,
  keep_metadata: true,
  keep_attachments: true,
  faststart: true,
  rate_control: "quality",
  constant_quality: null,
  bitrate_kbps: null,
  encoder_preset: null,
  extra_video_args: [],
};

/** Compare two specs, ignoring fields a starting point doesn't set explicitly and array identity. */
export function sameSpec(a: ProfileSpec, b: ProfileSpec): boolean {
  const norm = (s: ProfileSpec) => JSON.stringify({ ...DEFAULT_SPEC, ...s, version: 0, extra_video_args: s.extra_video_args ?? [] });
  return norm(a) === norm(b);
}

/** One line, e.g. "MP4 · H.265 · Balanced (68) · audio kept". */
export function specSummary(spec: ProfileSpec, options?: ProfileOptions): string {
  const parts = [spec.container.toUpperCase(), spec.video_codec === "copy" ? "video copied" : codec(spec.video_codec)];
  if (spec.video_codec !== "copy") {
    if (spec.rate_control === "bitrate") parts.push(`${spec.bitrate_kbps ?? "?"} kb/s`);
    else if (spec.rate_control === "constant_quality") parts.push(`value ${spec.constant_quality ?? "?"}`);
    else {
      const tier = [...(options?.quality_tiers ?? [])].reverse().find((t) => spec.quality >= t.min);
      parts.push(`${tier ? tier.label : "quality"} (${spec.quality})`);
    }
    if (spec.max_resolution) parts.push(`≤${spec.max_resolution}p`);
    if (spec.max_fps) parts.push(`≤${spec.max_fps} fps`);
  }
  const audio = options?.audio_codecs.find((a) => a.value === spec.audio_codec)?.label.split(" (")[0] ?? spec.audio_codec.toUpperCase();
  parts.push(spec.audio_mode === "transcode" ? `audio → ${audio}` : "audio kept");
  return parts.join(" · ");
}
