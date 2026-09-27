// Draft state for the first-run wizard. Nothing here is sent to the server until Complete Setup,
// except the admin account (every other API needs the session that account creates).

import type {
  AgingPolicy,
  AgingStage,
  ConditionLeaf,
  FieldDef,
  GeneralSettings,
  Library,
  Node,
  PathMapping,
  Profile,
  ProfileSpec,
  TimeWindow,
  ValidationThresholds,
} from "../../api/types";
import { DEFAULT_VALIDATION } from "../Libraries";
import { DEFAULT_STORAGE, RETENTION_LIMITS, storageFromLegacy, usesBackupFolder, usesOutputFolder, writesSeparateFile, type StorageValue } from "../../lib/storage";

export type StepId = "welcome" | "admin" | "storage" | "transcoding" | "rules" | "nodes" | "schedule" | "review";

export const STEPS: { id: StepId; label: string }[] = [
  { id: "welcome", label: "Welcome" },
  { id: "admin", label: "Administrator" },
  { id: "storage", label: "Storage" },
  { id: "transcoding", label: "Transcoding" },
  { id: "rules", label: "Rules" },
  { id: "nodes", label: "Nodes" },
  { id: "schedule", label: "Schedule" },
  { id: "review", label: "Review" },
];

export const stepIndex = (id: StepId) => STEPS.findIndex((s) => s.id === id);

export type QuietHours = GeneralSettings["quiet_hours"];

export const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
export const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const OVERNIGHT: TimeWindow = { start: "23:00", end: "07:00", days: ALL_DAYS };

// Mirrors the server's limits (api/libraries.py, api/rules.py, api/nodes.py).
export const SCAN_INTERVAL = { min: 5, max: 10080 };
export const MAX_AGING_STAGES = 8;
export const MAX_AGE_DAYS = 36500;
export const CONCURRENCY = { min: 1, max: 16 };
export const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/;
export const USERNAME_LENGTH = { min: 2, max: 64 };
export const PASSWORD_LENGTH = { min: 8, max: 256 };

export interface LibraryDraft {
  key: string;
  name: string;
  paths: string[];
  storage: StorageValue;
  output_path: string;
  backup_path: string;
  scan_interval_minutes: number | null;
  exclude_patterns: string; // one per line
  validation: ValidationThresholds;
}

export interface AgingDraft {
  enabled: boolean;
  age_field: AgingPolicy["age_field"];
  stages: AgingStage[];
  excluded: string[]; // library keys the policy should not be created for
}

/** A rule checked before the aging policy (codec filters, protected folders, …). Conditions are AND-ed. */
export interface ExceptionDraft {
  key: string;
  name: string;
  library_key: string | null; // null = all libraries
  conditions: ConditionLeaf[];
  action: "skip" | "transcode";
  profile_id: number | null;
  priority: number;
}

export interface NodeDraft {
  max_concurrency: number | null;
  reserve_slot_for_normal: boolean;
  max_gpu_util: number | null;
  max_cpu_util: number | null;
  window: TimeWindow | null;
  path_mappings: PathMapping[];
}

export interface SetupDraft {
  v: 1;
  step: StepId;
  visited: StepId[];
  libraries: LibraryDraft[];
  automation: "observe" | "auto";
  archiveProfileId: number | null;
  profileEdits: Record<number, ProfileSpec>;
  aging: AgingDraft;
  exceptions: ExceptionDraft[];
  nodes: Record<number, NodeDraft>; // only nodes the admin touched
  publicUrl: string | null; // null = keep the server's value
  quietHours: QuietHours | null; // null = keep the server's value
  policyWindow: TimeWindow | null;
  /** Commit tasks that already succeeded, with the id they created (libraries, rules). */
  committed: Record<string, number | true>;
}

let seq = 0;
export const newKey = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export function newLibrary(first: boolean): LibraryDraft {
  return {
    key: newKey("lib"),
    name: first ? "Recordings" : "",
    paths: [first ? "/media" : "/media/"],
    storage: { ...DEFAULT_STORAGE },
    output_path: "",
    backup_path: "",
    scan_interval_minutes: 60,
    exclude_patterns: "",
    validation: { ...DEFAULT_VALIDATION },
  };
}

export function initialDraft(): SetupDraft {
  return {
    v: 1,
    step: "welcome",
    visited: [],
    libraries: [newLibrary(true)],
    automation: "observe",
    archiveProfileId: null,
    profileEdits: {},
    aging: { enabled: true, age_field: "file_age_days", stages: [], excluded: [] },
    exceptions: [],
    nodes: {},
    publicUrl: null,
    quietHours: null,
    policyWindow: null,
    committed: {},
  };
}

// ------------------------------------------------------------------ persistence (per tab, never the password)

const STORAGE_KEY = "ff-setup-draft";

export function loadDraft(): SetupDraft | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SetupDraft;
    if (parsed?.v !== 1) return null;
    // Drafts saved before the storage split carry output_policy instead of storage.
    const libraries = (parsed.libraries ?? []).map((l) => {
      const legacy = l as LibraryDraft & { output_policy?: Parameters<typeof storageFromLegacy>[0] };
      return l.storage ? l : { ...l, storage: storageFromLegacy(legacy.output_policy, legacy.output_path) };
    });
    return { ...initialDraft(), ...parsed, libraries };
  } catch {
    return null;
  }
}

export function saveDraft(draft: SetupDraft) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  } catch {
    /* storage unavailable: the draft still lives in memory */
  }
}

export function clearDraft() {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

// ------------------------------------------------------------------ profiles

export function defaultArchiveProfile(profiles: Profile[]): Profile | undefined {
  const byKey = (...keys: string[]) => keys.map((k) => profiles.find((p) => p.builtin_key === k)).find(Boolean);
  return byKey("balanced", "youtube_archive") ?? profiles.find((p) => p.spec.video_codec === "hevc") ?? profiles.find((p) => p.spec.video_codec !== "copy") ?? profiles[0];
}

export function longTermProfile(profiles: Profile[], archiveId: number | null): Profile | undefined {
  const byKey = (...keys: string[]) => keys.map((k) => profiles.find((p) => p.builtin_key === k)).find(Boolean);
  return byKey("smallest_av1", "long_term_av1") ?? profiles.find((p) => p.spec.video_codec === "av1" && p.id !== archiveId);
}

/** Pick the archive profile and seed the aging stages once profiles are known. */
export function withProfileDefaults(d: SetupDraft, profiles: Profile[]): SetupDraft {
  const archive = defaultArchiveProfile(profiles);
  if (!archive) return d;
  return {
    ...d,
    archiveProfileId: archive.id,
    aging: d.aging.stages.length
      ? d.aging
      : {
          ...d.aging,
          stages: [
            { min_days: 0, action: "keep", profile_id: null, priority: 2 },
            { min_days: 30, action: "transcode", profile_id: archive.id, priority: 2 },
          ],
        },
  };
}

export const effectiveSpec = (d: SetupDraft, p: Profile): ProfileSpec => d.profileEdits[p.id] ?? p.spec;
export const specChanged = (a: ProfileSpec, b: ProfileSpec) => JSON.stringify(a) !== JSON.stringify(b);

// ------------------------------------------------------------------ nodes

export function nodeDraftFrom(node: Node): NodeDraft {
  const c = node.constraints ?? {};
  return {
    max_concurrency: node.max_concurrency,
    reserve_slot_for_normal: node.reserve_slot_for_normal,
    max_gpu_util: c.max_gpu_util ?? null,
    max_cpu_util: c.max_cpu_util ?? null,
    window: c.window ? { ...c.window, days: c.window.days ?? ALL_DAYS } : null,
    path_mappings: node.path_mappings ?? [],
  };
}

export const nodeDraft = (d: SetupDraft, node: Node): NodeDraft => d.nodes[node.id] ?? nodeDraftFrom(node);

export function nodePatch(n: NodeDraft) {
  return {
    max_concurrency: n.max_concurrency ?? 1,
    reserve_slot_for_normal: n.reserve_slot_for_normal,
    constraints: { max_gpu_util: n.max_gpu_util, max_cpu_util: n.max_cpu_util, window: n.window },
    path_mappings: n.path_mappings.filter((m) => m.server.trim() && m.node.trim()),
  };
}

export function nodeChanged(d: SetupDraft, node: Node): boolean {
  const draft = d.nodes[node.id];
  return !!draft && JSON.stringify(nodePatch(draft)) !== JSON.stringify(nodePatch(nodeDraftFrom(node)));
}

// ------------------------------------------------------------------ settings

export const effectiveQuietHours = (d: SetupDraft, s: GeneralSettings | undefined): QuietHours | null => d.quietHours ?? s?.quiet_hours ?? null;
export const effectivePublicUrl = (d: SetupDraft, s: GeneralSettings | undefined): string => d.publicUrl ?? s?.public_url ?? "";

export function settingsChanged(d: SetupDraft, s: GeneralSettings | undefined): boolean {
  if (!s) return false;
  const url = (d.publicUrl ?? s.public_url ?? "").trim() || null;
  return url !== (s.public_url ?? null) || (d.quietHours != null && JSON.stringify(d.quietHours) !== JSON.stringify(s.quiet_hours));
}

// ------------------------------------------------------------------ time windows (port of services/timewindow.py)

const minutesOf = (hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number);
  return ((h || 0) % 24) * 60 + ((m || 0) % 60);
};

/** Same semantics as the server: wrapped windows belong to the day they start on; no days = every day. */
export function inWindow(w: TimeWindow | null | undefined, weekday: number, minute: number): boolean {
  if (!w) return true;
  const start = minutesOf(w.start);
  const end = minutesOf(w.end);
  let inside: boolean;
  let startDay = weekday;
  if (start === end) inside = true;
  else if (start < end) inside = minute >= start && minute < end;
  else if (minute >= start) inside = true;
  else if (minute < end) {
    inside = true;
    startDay = (weekday + 6) % 7;
  } else inside = false;
  if (!inside) return false;
  return !w.days || w.days.length === 0 || w.days.includes(startDay);
}

export const validTime = (t: string | undefined) => !!t && /^\d{2}:\d{2}$/.test(t);

// ------------------------------------------------------------------ folder checks

export type FolderState = { state: "checking" } | { state: "found"; dirs: number } | { state: "missing" } | { state: "denied" } | { state: "unknown"; message: string };

export const isAbsolute = (p: string) => p.trim().replace(/\\/g, "/").startsWith("/");
export const normPath = (p: string) => {
  const s = p.trim().replace(/\\/g, "/").replace(/\/+$/, "");
  return s || "/";
};

/** Every folder the drafts reference, so the shell can check them against the container's filesystem. */
export function draftPaths(d: SetupDraft): string[] {
  const out = new Set<string>();
  for (const lib of d.libraries) {
    for (const p of lib.paths) if (isAbsolute(p)) out.add(normPath(p));
    if (usesOutputFolder(lib.storage) && isAbsolute(lib.output_path)) out.add(normPath(lib.output_path));
    if (usesBackupFolder(lib.storage) && isAbsolute(lib.backup_path)) out.add(normPath(lib.backup_path));
  }
  return [...out].sort();
}

const within = (child: string, parent: string) => parent === "/" || child === parent || child.startsWith(parent + "/");

// ------------------------------------------------------------------ validation

export type Issue = { level: "error" | "warn"; text: string; field?: string; key?: string };

export interface ServerData {
  profiles?: Profile[];
  nodes?: Node[];
  libraries?: Library[];
  settings?: GeneralSettings;
  fields?: FieldDef[];
}

export function libraryIssues(lib: LibraryDraft, all: LibraryDraft[], existing: Library[] | undefined, folders: Record<string, FolderState>): Issue[] {
  const out: Issue[] = [];
  const add = (level: Issue["level"], field: string, text: string) => out.push({ level, field, text, key: lib.key });
  const name = lib.name.trim();
  if (!name) add("error", "name", "Give the library a name.");
  else if (name.length > 128) add("error", "name", "Use at most 128 characters.");
  else if (all.some((o) => o.key !== lib.key && o.name.trim() === name)) add("error", "name", "Another library in this setup already uses this name.");
  else if (existing?.some((e) => e.name === name)) add("error", "name", "A library with this name already exists on this server.");

  const paths = lib.paths.map((p) => p.trim());
  if (!paths.some(Boolean)) add("error", "path:0", "Add at least one folder.");
  paths.forEach((p, i) => {
    if (!p) return;
    const f = `path:${i}`;
    if (!isAbsolute(p)) return add("error", f, "Use an absolute path inside the container, e.g. /media/vods.");
    const st = folders[normPath(p)];
    if (st?.state === "missing") add("error", f, "Not found inside the container. Check the volume mounts in docker-compose.yml.");
    else if (st?.state === "denied") add("warn", f, "The container can't list this folder (permission denied). Check PUID/PGID.");
    if (paths.findIndex((q) => q && normPath(q) === normPath(p)) !== i) add("warn", f, "This folder is listed twice.");
    for (const other of all) {
      if (other.key === lib.key) continue;
      const clash = other.paths.find((q) => isAbsolute(q) && (within(normPath(p), normPath(q)) || within(normPath(q), normPath(p))));
      if (clash) add("warn", f, `Overlaps with “${other.name || "another library"}” (${normPath(clash)}). Its files would be scanned and evaluated twice.`);
    }
  });

  const s = lib.storage;
  if (usesOutputFolder(s)) {
    if (!lib.output_path.trim()) add("error", "output_path", "Choose the folder converted files are written to.");
    else if (!isAbsolute(lib.output_path)) add("error", "output_path", "Use an absolute path inside the container.");
    else if (paths.some((p) => p && normPath(p) === normPath(lib.output_path))) add("error", "output_path", "Use a folder other than the library's own folders.");
  }
  if (usesBackupFolder(s) && lib.backup_path.trim() && !isAbsolute(lib.backup_path)) add("error", "backup_path", "Use an absolute path inside the container.");
  if (s.original_handling === "keep_days" && (s.retention_days == null || s.retention_days < RETENTION_LIMITS.min || s.retention_days > RETENTION_LIMITS.max))
    add("error", "retention", `Keep originals for ${RETENTION_LIMITS.min}–${RETENTION_LIMITS.max} days.`);

  const iv = lib.scan_interval_minutes;
  if (iv == null || iv < SCAN_INTERVAL.min || iv > SCAN_INTERVAL.max) add("error", "interval", `Scan every ${SCAN_INTERVAL.min}–${SCAN_INTERVAL.max} minutes.`);

  const v = lib.validation;
  if (!(v.duration_tolerance_pct > 0)) add("error", "duration", "Allow a duration difference above 0%.");
  if (v.min_output_bytes < 0) add("error", "min_size", "The minimum size can't be negative.");
  if (v.max_size_ratio != null && v.max_size_ratio < 0.1) add("error", "size", "Use a size limit of at least 10%, or leave it empty to turn the check off.");
  if (v.max_size_ratio == null) add("warn", "size", "Size check off: outputs larger than the original are accepted.");
  else if (!v.fail_if_larger && s.output_location === "source_folder" && (s.original_handling === "delete" || s.kept_original_location === "backup"))
    add("warn", "size", "Outputs over the size limit will still replace the original (with a warning).");
  return out;
}

export function exceptionIssues(r: ExceptionDraft, fields: FieldDef[] | undefined): Issue[] {
  const out: Issue[] = [];
  const add = (field: string, text: string) => out.push({ level: "error", field, text, key: r.key });
  if (!r.name.trim()) add("name", "Name this rule.");
  if (r.conditions.length === 0) add("conditions", "Add at least one condition.");
  r.conditions.forEach((c, i) => {
    const f = fields?.find((x) => x.key === c.field);
    if (!f) return;
    const empty = c.value == null || c.value === "" || (Array.isArray(c.value) && c.value.length === 0);
    if (f.kind !== "bool" && empty) add(`cond:${i}`, `${f.label}: enter a value.`);
  });
  if (r.action === "transcode" && r.profile_id == null) add("profile", "Choose a profile.");
  return out;
}

export function agingIssues(d: SetupDraft): Issue[] {
  const out: Issue[] = [];
  if (!d.aging.enabled) return out;
  const stages = d.aging.stages;
  const days = stages.map((s) => s.min_days);
  const targets = d.libraries.filter((l) => !d.aging.excluded.includes(l.key));
  if (d.libraries.length === 0) out.push({ level: "warn", text: "Aging policies belong to a library. Add one under Storage, or the policy is skipped." });
  else if (targets.length === 0) out.push({ level: "warn", text: "The policy isn't applied to any library, so it will be skipped." });
  if (stages.length === 0) out.push({ level: "error", text: "Add at least one stage." });
  if (new Set(days).size !== days.length) out.push({ level: "error", field: "stages", text: "Two stages start at the same age. Each stage needs its own starting day." });
  stages.forEach((s, i) => {
    if (s.min_days < 0 || s.min_days > MAX_AGE_DAYS) out.push({ level: "error", field: `stage:${i}`, text: `Stage ages run from 0 to ${MAX_AGE_DAYS} days.` });
    if (s.action === "transcode" && s.profile_id == null) out.push({ level: "error", field: `stage:${i}`, text: "Choose a profile for this stage." });
  });
  const converts = stages.filter((s) => s.action === "transcode").length;
  if (stages.length > 0 && converts === 0) out.push({ level: "warn", text: "Every stage keeps files, so nothing will be converted." });
  if (converts > 1) {
    for (const lib of targets.filter((l) => writesSeparateFile(l.storage))) {
      out.push({
        level: "warn",
        text: `“${lib.name || "Library"}” writes outputs to a separate file. A later stage re-encodes the original to the same output name and fails safely with “A file already exists”. Aging stages are designed for the replace policies.`,
      });
    }
  }
  return out;
}

export function nodeIssues(d: SetupDraft, node: Node): Issue[] {
  const n = nodeDraft(d, node);
  const out: Issue[] = [];
  const add = (field: string, text: string) => out.push({ level: "error", field, text, key: String(node.id) });
  if (n.max_concurrency == null || n.max_concurrency < CONCURRENCY.min || n.max_concurrency > CONCURRENCY.max) add("concurrency", `Run ${CONCURRENCY.min}–${CONCURRENCY.max} jobs at the same time.`);
  for (const [k, v] of [["gpu", n.max_gpu_util], ["cpu", n.max_cpu_util]] as const) if (v != null && (v < 1 || v > 100)) add(k, "Use 1–100%, or leave it empty.");
  if (n.window) {
    if (!validTime(n.window.start) || !validTime(n.window.end)) add("window", "Enter a start and end time.");
    else if (n.window.days && n.window.days.length === 0) add("window", "Pick at least one day.");
  }
  n.path_mappings.forEach((m, i) => {
    const partial = !!m.server.trim() !== !!m.node.trim();
    if (partial) add(`map:${i}`, "Fill in both sides of the mapping, or remove it.");
    else if (m.server.trim() && (!isAbsolute(m.server) || !isAbsolute(m.node))) add(`map:${i}`, "Both paths must be absolute.");
  });
  return out;
}

export function windowIssue(w: TimeWindow | null | undefined): string | null {
  if (!w) return null;
  if (!validTime(w.start) || !validTime(w.end)) return "Enter a start and end time.";
  if (w.days && w.days.length === 0) return "Pick at least one day.";
  return null;
}

export function computeIssues(d: SetupDraft, server: ServerData, folders: Record<string, FolderState>, authed: boolean): Record<StepId, Issue[]> {
  const r: Record<StepId, Issue[]> = { welcome: [], admin: [], storage: [], transcoding: [], rules: [], nodes: [], schedule: [], review: [] };
  if (!authed) r.admin.push({ level: "error", text: "Create the administrator account." });

  // Items an earlier Complete Setup attempt already created are done; they must not conflict with themselves.
  const createdIds = new Set(Object.entries(d.committed).filter(([k]) => k.startsWith("library:")).map(([, v]) => v));
  const existing = server.libraries?.filter((l) => !createdIds.has(l.id));
  if (d.libraries.length === 0) r.storage.push({ level: "warn", text: "No libraries: FrameForge won't scan anything until you add one." });
  for (const lib of d.libraries) if (!(`library:${lib.key}` in d.committed)) r.storage.push(...libraryIssues(lib, d.libraries, existing, folders));

  const archive = server.profiles?.find((p) => p.id === d.archiveProfileId);
  if (server.profiles && server.profiles.length === 0) r.transcoding.push({ level: "warn", text: "There are no profiles. Create one under Profiles after setup." });
  for (const [id, spec] of Object.entries(d.profileEdits)) {
    const p = server.profiles?.find((x) => x.id === Number(id));
    if (p && (spec.audio_bitrate_kbps < 32 || spec.audio_bitrate_kbps > 1024)) r.transcoding.push({ level: "error", field: "audio", key: id, text: `${p.name}: audio bitrate must be 32–1024 kb/s.` });
  }
  if (archive && server.nodes) {
    const spec = effectiveSpec(d, archive);
    const hw = spec.hw_mode;
    if (spec.video_codec !== "copy" && hw !== "auto" && hw !== "cpu" && !spec.allow_cpu_fallback) {
      const verified = server.nodes.some((n) => n.capabilities?.encoders.some((e) => e.backend === hw && e.codec === spec.video_codec && e.verified));
      if (!verified) r.transcoding.push({ level: "warn", field: "hardware", text: `No node has verified a ${hw.toUpperCase()} encoder for this codec and CPU fallback is off, so these jobs will wait.` });
    }
  }

  r.rules.push(...agingIssues(d));
  for (const e of d.exceptions) if (!(`rule:${e.key}` in d.committed)) r.rules.push(...exceptionIssues(e, server.fields));

  const url = effectivePublicUrl(d, server.settings).trim();
  if (url && !/^https?:\/\/[^\s/]+/i.test(url)) r.nodes.push({ level: "error", field: "public_url", text: "Use a full address such as http://192.168.1.20:8686." });
  if (server.nodes && server.nodes.length === 0) r.nodes.push({ level: "warn", text: "No nodes are registered, so jobs can't run yet. Add a remote node here or after setup." });
  for (const n of server.nodes ?? []) r.nodes.push(...nodeIssues(d, n).filter((i) => i.field?.startsWith("map:")));

  const qh = effectiveQuietHours(d, server.settings);
  const qhIssue = qh?.enabled ? windowIssue(qh.window) : null;
  if (qhIssue) r.schedule.push({ level: "error", field: "quiet", text: `Background work hours: ${qhIssue}` });
  const pwIssue = d.aging.enabled ? windowIssue(d.policyWindow) : null;
  if (pwIssue) r.schedule.push({ level: "error", field: "policy_window", text: `Aging window: ${pwIssue}` });
  for (const n of server.nodes ?? []) r.schedule.push(...nodeIssues(d, n).filter((i) => !i.field?.startsWith("map:")).map((i) => ({ ...i, text: `${n.name}: ${i.text}` })));

  return r;
}

export const errorsIn = (issues: Issue[]) => issues.filter((i) => i.level === "error");
export function fieldIssue(issues: Issue[], field: string, key?: string): Issue | undefined {
  const matches = issues.filter((i) => i.field === field && (key === undefined || i.key === key));
  return matches.find((i) => i.level === "error") ?? matches[0];
}
