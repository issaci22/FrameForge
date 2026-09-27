const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function bytes(n: number | null | undefined, digits = 1): string {
  if (n == null || !isFinite(n)) return "—";
  const neg = n < 0;
  let v = Math.abs(n);
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  const s = `${v.toFixed(i === 0 ? 0 : v >= 100 ? 0 : digits)} ${UNITS[i]}`;
  return neg ? `−${s}` : s;
}

export function rate(bps: number | null | undefined): string {
  if (bps == null || !isFinite(bps)) return "—";
  return `${bytes(bps)}/s`;
}

/** 3725 → "1h 02m", 125 → "2m 05s" */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

/** 3725 → "01:02:05" (timecode style) */
export function timecode(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "--:--";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h ? `${String(h).padStart(2, "0")}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  const days = Math.round(diff / 86400);
  if (days < 60) return `${days} days ago`;
  if (days < 730) return `${Math.round(days / 30)} months ago`;
  return `${(days / 365).toFixed(1)} years ago`;
}

export function ageDays(iso: string): number {
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function pct(n: number | null | undefined, digits = 0): string {
  if (n == null || !isFinite(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

const CODEC_LABELS: Record<string, string> = {
  h264: "H.264",
  hevc: "H.265",
  av1: "AV1",
  vp9: "VP9",
  vp8: "VP8",
  mpeg2video: "MPEG-2",
  mpeg4: "MPEG-4",
  prores: "ProRes",
  dnxhd: "DNxHD",
  copy: "Remux",
};

export function codec(c: string | null | undefined): string {
  if (!c) return "—";
  return CODEC_LABELS[c] ?? c.toUpperCase();
}

export const PRIORITIES = ["Background", "Low", "Normal", "High", "Critical"];
export const priorityName = (p: number) => PRIORITIES[p] ?? String(p);

export const BACKEND_LABELS: Record<string, string> = {
  cpu: "CPU",
  nvenc: "NVIDIA",
  qsv: "Intel QSV",
  vaapi: "VA-API",
  amf: "AMD AMF",
};

export function savedRatio(before: number | null | undefined, after: number | null | undefined): number | null {
  if (!before || after == null) return null;
  return (1 - after / before) * 100;
}
