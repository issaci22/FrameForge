// Mirrors the server's /api/v1 response shapes. Keep in sync with server/frameforge_server/api/*.

export type JobState =
  | "queued"
  | "assigned"
  | "preparing"
  | "transcoding"
  | "validating"
  | "finalizing"
  | "completed"
  | "failed"
  | "cancelled";

export const ACTIVE_STATES: JobState[] = ["assigned", "preparing", "transcoding", "validating", "finalizing"];

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
}

export interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  user: User | null;
}

export interface Job {
  id: number;
  file_id: number | null;
  library_id: number | null;
  filename: string;
  source_path: string;
  profile_id: number | null;
  profile_name: string;
  rule_name: string | null;
  priority: number;
  manual: boolean;
  state: JobState;
  waiting_reason: string | null;
  node_id: number | null;
  node_name: string | null;
  encoder: string | null;
  backend: string | null;
  hw_decode: boolean | null;
  attempts: number;
  source_codec: string | null;
  target_codec: string | null;
  resolution: string | null;
  source_duration: number | null;
  source_size: number | null;
  output_size: number | null;
  output_path: string | null;
  progress: number;
  fps: number | null;
  speed: number | null;
  eta_seconds: number | null;
  error_code: string | null;
  error_title: string | null;
  created_at: string | null;
  assigned_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  // live-only
  frame?: number | null;
  bitrate_kbps?: number | null;
  out_size?: number | null;
  elapsed?: number | null;
  gpu_util?: number | null;
}

export interface Diagnosis {
  code: string;
  title: string;
  explanation: string;
  causes: string[];
  technical: string | null;
}

export interface ValidationCheck {
  name: string;
  passed: boolean;
  detail: string;
  severity: "error" | "warning";
}

export interface JobEvent {
  id: number;
  at: string;
  level: "info" | "warn" | "error";
  kind: string;
  message: string;
  data: Record<string, unknown>;
}

export interface FinalizePlan {
  source: string;
  temp_output: string;
  final_output: string;
  original_action: "keep" | "delete" | "backup";
  backup_path: string | null;
}

export interface JobDetail extends Job {
  profile_spec: ProfileSpec;
  diagnosis: Diagnosis | null;
  validation: { passed: boolean; checks: ValidationCheck[] } | null;
  notes: string[];
  finalize_plan: FinalizePlan | null;
  events: JobEvent[];
}

export interface JobList {
  total: number;
  items: Job[];
  counts: Record<"active" | "queued" | "completed" | "failed" | "cancelled" | "finished", number>;
}

export interface ValidationThresholds {
  duration_tolerance_pct: number;
  min_output_bytes: number;
  max_size_ratio: number | null;
  fail_if_larger: boolean;
  require_audio_if_source_has_audio: boolean;
}

/** Legacy single storage value; derived from the fields below (see lib/storage.ts). */
export type OutputPolicy = "replace" | "backup" | "output_dir" | "alongside";
export type OutputLocation = "source_folder" | "folder";
export type OriginalHandling = "keep" | "delete" | "keep_days";
export type KeptOriginalLocation = "backup" | "in_place";

export interface RetentionSummary {
  pending: number;
  blocked: number;
  bytes: number;
  next_due: string | null;
}

export interface RetainedOriginal {
  id: number;
  library_id: number | null;
  job_id: number;
  original_path: string;
  filename: string;
  original_size: number;
  output_path: string;
  output_size: number | null;
  created_at: string;
  due_at: string;
  state: "pending" | "blocked" | "deleted" | "kept" | "gone";
  reason: string | null;
  paused: string | null;
  checked_at: string | null;
  deleted_at: string | null;
}

export interface ScanState {
  phase?: string;
  message?: string;
  discovered?: number;
  probed?: number;
  to_probe?: number;
  summary?: Record<string, unknown>;
}

export interface Library {
  id: number;
  name: string;
  paths: string[];
  enabled: boolean;
  automation_enabled: boolean;
  scan_interval_minutes: number;
  exclude_patterns: string[];
  output_policy: OutputPolicy;
  output_location: OutputLocation;
  original_handling: OriginalHandling;
  retention_days: number | null;
  kept_original_location: KeptOriginalLocation;
  output_path: string | null;
  backup_path: string | null;
  effective_backup_path: string | null;
  retention?: RetentionSummary;
  validation: ValidationThresholds;
  created_at: string;
  last_scan_at: string | null;
  last_scan_summary: Record<string, unknown>;
  scanning: boolean;
  scan_state: ScanState | null;
  stats?: { files_by_status: Record<string, number>; files: number; original_bytes: number; current_bytes: number };
  warnings?: string[];
}

export interface MediaSummary {
  container: string;
  duration: number;
  bitrate: number | null;
  video_codec: string | null;
  video_codec_label: string;
  width: number | null;
  height: number | null;
  fps: number | null;
  bit_depth: number | null;
  hdr_format: string | null;
  audio_codecs: string[];
  audio_count: number;
  subtitle_count: number;
  chapter_count: number;
  creation_time: string | null;
  resolution_label: string | null;
}

export type FileStatus = "new" | "ready" | "queued" | "processing" | "processed" | "failed" | "error" | "missing";

export interface MediaFile {
  id: number;
  library_id: number;
  path: string;
  relative_path: string;
  filename: string;
  extension: string;
  size: number;
  original_size: number | null;
  mtime: string;
  status: FileStatus;
  ignored: boolean;
  decision: string | null;
  probe_error: string | null;
  processed_profile_id: number | null;
  processed_at: string | null;
  last_job_id: number | null;
  media: MediaSummary | null;
}

export interface TraceItem {
  label: string;
  passed: boolean;
  actual: string | null;
  depth: number;
}

export interface FileDetail extends MediaFile {
  info: Record<string, unknown> | null;
  evaluation: {
    action: "transcode" | "skip" | "none";
    reason: string;
    rule_id: number | null;
    profile_id: number | null;
    rules: { rule_id: number; rule_name: string; matched: boolean; trace: TraceItem[] }[];
  };
  jobs: Job[];
}

export type AudioCodec = "aac" | "opus" | "ac3" | "eac3" | "flac";

export interface ProfileSpec {
  version: number;
  engine: "ffmpeg" | "handbrake";
  container: "mkv" | "mp4";
  video_codec: "h264" | "hevc" | "av1" | "copy";
  quality: number;
  speed: "fast" | "balanced" | "quality" | "max";
  max_resolution: number | null;
  max_fps: number | null;
  hw_mode: "auto" | "cpu" | "nvenc" | "qsv" | "vaapi" | "amf";
  allow_cpu_fallback: boolean;
  hw_decode: boolean;
  ten_bit: "auto" | "never";
  audio_mode: "copy" | "copy_compatible" | "transcode";
  audio_codec: AudioCodec;
  audio_bitrate_kbps: number;
  audio_copy_scope: "storable" | "widely_playable";
  keep_subtitles: boolean;
  keep_chapters: boolean;
  keep_metadata: boolean;
  keep_attachments: boolean;
  faststart: boolean;
  rate_control: "quality" | "constant_quality" | "bitrate";
  constant_quality: number | null;
  bitrate_kbps: number | null;
  encoder_preset: string | null;
  extra_video_args: string[];
}

export interface Profile {
  id: number;
  name: string;
  description: string;
  builtin: boolean;
  builtin_key: string | null;
  /** "goal" starting points, "special" purpose built-ins, "legacy" built-ins from the first lineup. */
  builtin_group: "goal" | "special" | "legacy" | null;
  based_on: string | null;
  spec: ProfileSpec;
  summary: string;
  rule_count: number;
  /** What the profile really did to completed jobs since its last change (ratio only with enough jobs). */
  history: { jobs: number; ratio: number | null } | null;
  updated_at: string;
}

export interface CompatIssue {
  level: "error" | "warn" | "info";
  field: string;
  message: string;
  suggestion: Partial<ProfileSpec> | null;
  suggestion_label: string | null;
}

export interface ProfilePreview {
  engine_available: boolean;
  message?: string;
  issues: CompatIssue[];
  tier: { key: string; label: string; description: string };
  compression_preview: { available: boolean; reason: string | null; node?: string };
  quality: { backend: string; label: string; encoder: string; value: number; param: string; range: [number, number] }[];
  commands: { backend: string; label?: string; command?: string; pipeline?: string[]; notes?: string[]; error?: string }[];
  nodes?: { node_id: number; name: string; encoder: string | null; backend: string | null; native: string | null; reason: string | null }[];
  sample?: string;
}

export interface StartingPoint {
  key: string;
  name: string;
  description: string;
  tagline: string;
  group: "goal" | "special";
  spec: ProfileSpec;
}

export interface ProfileOptions {
  containers: { value: "mkv" | "mp4"; label: string; description: string; video_codecs: string[]; audio_codecs: AudioCodec[]; audio_copy: string[]; audio_playable: string[]; subtitles: string; attachments: boolean }[];
  video_codecs: { value: ProfileSpec["video_codec"]; label: string; description: string; efficiency: string; keeps_hdr: boolean }[];
  audio_codecs: { value: AudioCodec; label: string; description: string; default_kbps: number | null; max_kbps: number | null; max_channels: number | null; lossless: boolean }[];
  starting_points: StartingPoint[];
  quality_tiers: { key: string; label: string; description: string; min: number }[];
  /** codec -> backend -> names of online nodes with a verified encoder */
  hardware: Record<string, Record<string, string[]>>;
  online_nodes: number;
  backend_labels: Record<string, string>;
  notices: string[];
}

export interface CompressionPreview {
  id: string;
  file_id: number;
  filename: string;
  node_id: number;
  node_name: string;
  state: "running" | "done" | "failed" | "cancelled";
  done: number;
  total: number;
  message: string | null;
  encoder: string | null;
  quality_label: string | null;
  notes: string[];
  error: string | null;
  samples: { index: number; position: number; width: number; height: number; original_url: string; encoded_url: string }[];
  estimate: { video_bytes: number; video_bytes_low: number; video_bytes_high: number; ratio: number | null; basis: string } | null;
}

export interface ConditionLeaf {
  type: "condition";
  field: string;
  operator: string;
  value: unknown;
}

export interface ConditionGroup {
  type: "group";
  op: "all" | "any" | "none";
  children: (ConditionGroup | ConditionLeaf)[];
}

export interface RuleSchedule {
  window?: TimeWindow | null;
}

export interface TimeWindow {
  start: string;
  end: string;
  days?: number[];
}

export interface Rule {
  id: number;
  name: string;
  description: string;
  library_id: number | null;
  position: number;
  enabled: boolean;
  conditions: ConditionGroup;
  action: "transcode" | "skip";
  profile_id: number | null;
  profile_name: string | null;
  priority: number;
  priority_name: string;
  schedule: RuleSchedule;
  skip_if_target_codec: boolean;
  policy_group: string | null;
  updated_at: string;
}

export interface FieldDef {
  key: string;
  label: string;
  kind: "number" | "enum" | "text" | "bool" | "time" | "day";
  group: string;
  unit: string | null;
  options: { value: string | number; label: string }[] | null;
  help: string | null;
  allow_custom: boolean;
  operators: { key: string; label: string }[];
}

export interface AgingStage {
  min_days: number;
  action: "keep" | "transcode";
  profile_id: number | null;
  priority: number;
}

export interface AgingPolicy {
  library_id: number | null;
  age_field: "file_age_days" | "recorded_age_days";
  stages: AgingStage[];
  window: TimeWindow | null;
  enabled: boolean;
}

export interface GpuInfo {
  index: number;
  vendor: "nvidia" | "intel" | "amd" | "unknown";
  name: string;
  vram_total_mb: number | null;
  driver: string | null;
  device: string | null;
}

export interface EncoderCap {
  name: string;
  codec: string;
  backend: string;
  verified: boolean;
  error: string | null;
}

export interface DecoderCap {
  backend: string;
  codec: string;
  verified: boolean;
  error: string | null;
}

export interface Capabilities {
  cpu_model: string;
  cpu_threads: number;
  ram_total_mb: number;
  os: string;
  hostname: string;
  ffmpeg_version: string | null;
  engines: Record<string, boolean>;
  gpus: GpuInfo[];
  encoders: EncoderCap[];
  decoders: DecoderCap[];
  render_device: string | null;
  storage: { path: string; total_bytes: number; free_bytes: number }[];
  recommended_concurrency: number;
  notes: string[];
  detected_at: number;
}

export interface GpuMetrics {
  index: number;
  utilization: number | null;
  encoder_utilization: number | null;
  decoder_utilization: number | null;
  vram_used_mb: number | null;
  vram_total_mb: number | null;
  temperature_c: number | null;
}

export interface NodeMetrics {
  timestamp: number;
  cpu_percent: number;
  ram_used_mb: number;
  ram_total_mb: number;
  load_1m: number | null;
  gpus: GpuMetrics[];
  disk_read_bps: number | null;
  disk_write_bps: number | null;
  net_rx_bps: number | null;
  net_tx_bps: number | null;
}

export interface MetricPoint {
  t: number;
  cpu: number | null;
  ram: number | null;
  gpu: number | null;
  enc: number | null;
  vram: number | null;
}

export interface PathMapping {
  server: string;
  node: string;
}

export interface NodeConstraints {
  max_gpu_util?: number | null;
  max_cpu_util?: number | null;
  window?: TimeWindow | null;
}

export interface Node {
  id: number;
  name: string;
  is_local: boolean;
  enabled: boolean;
  paused: boolean;
  online: boolean;
  status: "online" | "offline" | "pending" | "paused" | "disabled";
  max_concurrency: number;
  reserve_slot_for_normal: boolean;
  constraints: NodeConstraints;
  path_mappings: PathMapping[];
  hardware_hint: string | null;
  token_hint: string;
  version: string | null;
  created_at: string;
  last_seen_at: string | null;
  capabilities: Capabilities | null;
  metrics: NodeMetrics | null;
  active_jobs: number[];
  history?: MetricPoint[];
}

export interface NodeEnrollment {
  node?: Node;
  token: string;
  server_url: string;
  compose: string;
  docker_run: string;
}

export interface Overview {
  jobs: { active: number; queued: number; completed_today: number; failed_today: number; completed_total: number; failed_total: number };
  storage: { processed_in: number; processed_out: number; saved: number; library_original: number; library_current: number; library_files: number };
  throughput_bps: number;
  eta_seconds: number | null;
  nodes: { online: number; total: number };
}

export interface HistoryDay {
  day: string;
  completed: number;
  failed: number;
  bytes_in: number;
  bytes_out: number;
  saved: number;
}

export interface GeneralSettings {
  quiet_hours: { enabled: boolean; window: TimeWindow; applies_to: "background" | "low_and_below" };
  max_attempts: number;
  probe_concurrency: number;
  job_history_days: number;
  public_url: string | null;
}

export interface SystemInfo {
  version: string;
  protocol: number;
  role: string;
  database: string;
  ffmpeg: string | null;
  python: string;
  platform: string;
  config_dir: string;
  engines: Record<string, { available: boolean; reason?: string }>;
}
