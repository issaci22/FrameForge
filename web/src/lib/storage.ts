// A library's storage policy: where converted files go, and what happens to the original.
// Mirrors server/frameforge_server/services/storage_policy.py.

import type { KeptOriginalLocation, OriginalHandling, OutputLocation, OutputPolicy } from "../api/types";

export interface StorageValue {
  output_location: OutputLocation;
  original_handling: OriginalHandling;
  retention_days: number | null;
  kept_original_location: KeptOriginalLocation;
}

export const RETENTION_CHOICES = [1, 3, 7, 14, 30];
export const RETENTION_LIMITS = { min: 1, max: 3650 };
export const BACKUP_DIR = ".frameforge-originals";

export const DEFAULT_STORAGE: StorageValue = { output_location: "source_folder", original_handling: "keep", retention_days: null, kept_original_location: "backup" };

export function storageFromLegacy(policy: OutputPolicy | undefined, outputPath?: string | null): StorageValue {
  if (policy === "replace") return { ...DEFAULT_STORAGE, original_handling: "delete" };
  if (policy === "alongside") return { ...DEFAULT_STORAGE, kept_original_location: "in_place" };
  if (policy === "output_dir" && outputPath) return { ...DEFAULT_STORAGE, output_location: "folder" };
  return { ...DEFAULT_STORAGE };
}

/** A library as returned by the API (older servers only send output_policy). */
export function storageOf(lib: Partial<StorageValue> & { output_policy?: OutputPolicy; output_path?: string | null }): StorageValue {
  if (!lib.output_location) return storageFromLegacy(lib.output_policy, lib.output_path);
  return {
    output_location: lib.output_location,
    original_handling: lib.original_handling ?? "keep",
    retention_days: lib.original_handling === "keep_days" ? lib.retention_days ?? null : null,
    kept_original_location: lib.kept_original_location ?? "backup",
  };
}

/** Request fields for POST/PUT /libraries. */
export function storageBody(s: StorageValue) {
  return {
    output_location: s.output_location,
    original_handling: s.original_handling,
    retention_days: s.original_handling === "keep_days" ? s.retention_days : null,
    kept_original_location: s.kept_original_location,
  };
}

export const usesOutputFolder = (s: StorageValue) => s.output_location === "folder";
/** The original is moved to a backup folder and the output takes its name. */
export const usesBackupFolder = (s: StorageValue) => s.output_location === "source_folder" && s.original_handling !== "delete" && s.kept_original_location === "backup";
/** Originals are deleted at some point (right away or after a period). */
export const deletesOriginals = (s: StorageValue) => s.original_handling !== "keep";
/** The output is a separate file next to or away from a kept original (so later aging stages can't re-encode into it). */
export const writesSeparateFile = (s: StorageValue) => s.original_handling !== "delete" && (s.output_location === "folder" || s.kept_original_location === "in_place");

const dayWord = (n: number | null) => (n === 1 ? "1 day" : `${n ?? "?"} days`);

/** Short label, e.g. "Same folder · keep 7 days". */
export function storageTitle(s: StorageValue): string {
  const where = s.output_location === "folder" ? "Separate folder" : "Same folder";
  const orig = s.original_handling === "delete" ? "delete original" : s.original_handling === "keep_days" ? `keep original ${dayWord(s.retention_days)}` : "keep original";
  return `${where} · ${orig}`;
}

/** One plain-language sentence spelling out what will happen on disk. */
export function storageSentence(s: StorageValue, opts: { outputPath?: string | null; backupPath?: string | null; ext?: string } = {}): string {
  const ext = opts.ext ?? ".mp4";
  const out = opts.outputPath?.trim() || "the output folder";
  const backup = opts.backupPath?.trim() || `a hidden ${BACKUP_DIR} folder in the library`;
  const period = dayWord(s.retention_days);
  if (s.output_location === "folder") {
    const where = `Converted files are written to ${out}, mirroring the library's sub-folders.`;
    if (s.original_handling === "delete") return `${where} Each original is deleted as soon as its converted file has been verified.`;
    if (s.original_handling === "keep_days") return `${where} Originals stay where they are for ${period} after a verified conversion, then are deleted once the converted file passes its checks again.`;
    return `${where} Originals are never touched.`;
  }
  if (s.original_handling === "delete") return `The converted file replaces the original in the same folder (“name${ext}”). The original is deleted only after the new file has been verified in its final place.`;
  if (s.kept_original_location === "in_place") {
    const where = `The converted file is saved next to the original as “name.ff${ext}”.`;
    if (s.original_handling === "keep_days") return `${where} The original stays for ${period}, then is deleted once the converted file passes its checks again.`;
    return `${where} Nothing is removed, so this needs space for both files.`;
  }
  const where = `The converted file takes the original's place, and the original moves to ${backup}.`;
  if (s.original_handling === "keep_days") return `${where} Backups are deleted ${period} after a verified conversion, once the converted file passes its checks again.`;
  return `${where} FrameForge never deletes backups, so on the same disk no space is freed until you remove them.`;
}
