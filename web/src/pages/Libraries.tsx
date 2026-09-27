import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Film, FolderOpen, FolderSearch, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { useAction, useLibraries } from "../api/hooks";
import type { Library, ValidationThresholds } from "../api/types";
import { PathPicker } from "../components/PathPicker";
import { RetainedOriginals } from "../components/RetainedOriginals";
import { StorageEditor } from "../components/StorageEditor";
import { Timeline } from "../components/media";
import { Callout, Empty, ErrorState, Field, LoadingState, Menu, Modal, NumberInput, PageHeader, Section, SettingRow, Toggle, useToast } from "../components/ui";
import { useLive } from "../live/events";
import { ago, bytes } from "../lib/format";
import { storageBody, storageOf, storageSentence, storageTitle, usesBackupFolder, usesOutputFolder, type StorageValue } from "../lib/storage";

const STATUS_TEXT: Record<string, string> = {
  new: "analyzing",
  ready: "analyzed",
  queued: "queued",
  processing: "processing",
  processed: "converted",
  failed: "failed",
  error: "unreadable",
  missing: "missing",
};

export function LibrariesPage() {
  const { data: libraries, error, refetch } = useLibraries();
  const [editing, setEditing] = useState<Library | "new" | null>(null);

  return (
    <div className="page">
      <PageHeader
        title="Libraries"
        description="Folders FrameForge watches. Each scan analyzes new files and applies your rules."
        actions={
          <button className="btn primary" onClick={() => setEditing("new")}>
            <Plus size={16} /> Add library
          </button>
        }
      />

      {error && !libraries ? (
        <ErrorState title="Couldn't load libraries" error={error} onRetry={() => refetch()} />
      ) : !libraries ? (
        <LoadingState />
      ) : libraries.length === 0 ? (
        <Section>
          <Empty
            icon={<FolderOpen size={20} />}
            title="No libraries yet"
            action={
              <button className="btn primary" onClick={() => setEditing("new")}>
                <Plus size={16} /> Add your first folder
              </button>
            }
          >
            Point FrameForge at a folder of recordings. It analyzes every video without changing anything until you enable rules.
          </Empty>
        </Section>
      ) : (
        <div className="stack gap-4">
          {libraries.map((lib) => (
            <LibraryCard key={lib.id} lib={lib} onEdit={() => setEditing(lib)} />
          ))}
        </div>
      )}

      {editing && <LibraryEditor library={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function LibraryCard({ lib, onEdit }: { lib: Library; onEdit: () => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const live = useLive((s) => s.scans[lib.id]);
  const scanning = live ? live.active : lib.scanning;
  const scan = useAction(() => api.post(`/libraries/${lib.id}/scan`), [["libraries"]]);
  const del = useAction(() => api.del(`/libraries/${lib.id}`), [["libraries"], ["files"]]);
  const stats = lib.stats;
  const saved = stats ? stats.original_bytes - stats.current_bytes : 0;
  const summary = lib.last_scan_summary as { found?: number; jobs_created?: number; still_recording?: number; errors?: string[]; error?: string };
  const storage = storageOf(lib);
  const scanPct = live?.to_probe ? ((live.probed ?? 0) / live.to_probe) * 100 : 0;
  const interval = lib.scan_interval_minutes >= 60 ? `${lib.scan_interval_minutes / 60} h` : `${lib.scan_interval_minutes} min`;

  return (
    <section className={`panel lib-card ${lib.enabled ? "" : "is-off"}`}>
      <div className="lib-head">
        <span className={`status-dot ${lib.enabled ? "online" : "offline"}`} title={lib.enabled ? "Enabled" : "Disabled"} />
        <div className="titles">
          <div className="row gap-2 wrap">
            <h2>{lib.name}</h2>
            {!lib.enabled && <span className="chip">Disabled</span>}
            {!lib.automation_enabled && <span className="chip warn">Automation off</span>}
          </div>
          <div className="mono small faint ellipsis" title={lib.paths.join(", ")}>
            {lib.paths.join(", ")}
          </div>
        </div>
        <div className="lib-actions">
          <button className="btn sm" disabled={scanning} onClick={() => scan.mutate(undefined, { onError: (e) => toast((e as Error).message, "err") })}>
            <RefreshCw size={14} className={scanning ? "spin" : undefined} /> {scanning ? "Scanning…" : "Scan now"}
          </button>
          <button className="btn sm" onClick={onEdit}>
            <Pencil size={14} /> Edit
          </button>
          <Menu
            label={`More actions for ${lib.name}`}
            items={[
              { label: "Browse files", icon: <Film size={14} />, onSelect: () => navigate(`/files?library=${lib.id}`) },
              "sep",
              {
                label: "Remove library",
                icon: <Trash2 size={14} />,
                danger: true,
                confirm: { text: `Remove “${lib.name}”?`, detail: "FrameForge stops watching these folders. Files on disk are not touched.", label: "Remove" },
                onSelect: () => del.mutate(undefined, { onSuccess: () => toast(`Removed ${lib.name}. Files on disk were not touched.`) }),
              },
            ]}
          />
        </div>
      </div>

      <div className="lib-body">
        <div className="facts">
          <div className="fact">
            <div className="k">Files</div>
            <div className="v">
              <Link to={`/files?library=${lib.id}`} className="mono">
                {stats?.files ?? 0}
              </Link>
            </div>
          </div>
          <div className="fact">
            <div className="k">Size now</div>
            <div className="v mono">
              {bytes(stats?.current_bytes)}
              {saved > 0 && <span className="small text-ok"> −{bytes(saved)}</span>}
            </div>
          </div>
          <div className="fact">
            <div className="k">Last scan</div>
            <div className="v small-v">
              {ago(lib.last_scan_at)} <span className="faint">· every {interval}</span>
            </div>
          </div>
          <div className="fact grow">
            <div className="k">After converting</div>
            <div className="v small-v" title={storageSentence(storage, { outputPath: lib.output_path, backupPath: lib.backup_path })}>
              {storageTitle(storage)}
              {usesBackupFolder(storage) && <span className="faint mono small"> → {lib.effective_backup_path}</span>}
              {usesOutputFolder(storage) && <span className="faint mono small"> → {lib.output_path}</span>}
            </div>
          </div>
        </div>

        {stats && Object.keys(stats.files_by_status).length > 0 && (
          <div className="row wrap gap-2">
            {Object.entries(stats.files_by_status).map(([k, v]) => (
              <Link key={k} to={`/files?library=${lib.id}&status=${k}`} className={`chip ${k === "error" || k === "failed" ? "err" : k === "processed" ? "ok" : k === "queued" || k === "processing" ? "accent" : ""}`}>
                {v} {STATUS_TEXT[k] ?? k}
              </Link>
            ))}
          </div>
        )}

        {scanning && (
          <div className="scan-progress">
            <div className="row small muted">{live?.message ?? "Scanning…"}</div>
            <Timeline value={scanPct} state={live?.to_probe ? "run" : "wait"} thin />
          </div>
        )}
        {!scanning && summary?.error && <Callout kind="err" title="Last scan failed">{summary.error}</Callout>}
        {!scanning && summary?.errors && summary.errors.length > 0 && (
          <Callout kind="warn" title="Some files couldn't be read">
            {summary.errors.slice(0, 3).join(" · ")}
          </Callout>
        )}
        {!scanning && !!summary?.still_recording && (
          <div className="faint small">{summary.still_recording} file(s) were modified in the last 2 minutes and will be picked up once they stop changing.</div>
        )}
        <RetainedOriginals lib={lib} />
      </div>
    </section>
  );
}

export const DEFAULT_VALIDATION: ValidationThresholds = {
  duration_tolerance_pct: 2,
  min_output_bytes: 1024 * 1024,
  max_size_ratio: 1.0,
  fail_if_larger: true,
  require_audio_if_source_has_audio: true,
};

function LibraryEditor({ library, onClose }: { library: Library | null; onClose: () => void }) {
  const toast = useToast();
  const [name, setName] = useState(library?.name ?? "");
  const [paths, setPaths] = useState<string[]>(library?.paths ?? ["/media"]);
  const [storage, setStorage] = useState<StorageValue>(library ? storageOf(library) : storageOf({ output_policy: "backup" }));
  const [outputPath, setOutputPath] = useState(library?.output_path ?? "");
  const [backupPath, setBackupPath] = useState(library?.backup_path ?? "");
  const [interval, setIntervalMin] = useState<number | null>(library?.scan_interval_minutes ?? 60);
  const [enabled, setEnabled] = useState(library?.enabled ?? true);
  const [automation, setAutomation] = useState(library?.automation_enabled ?? true);
  const [excludes, setExcludes] = useState((library?.exclude_patterns ?? []).join("\n"));
  const [validation, setValidation] = useState<ValidationThresholds>(library?.validation ?? DEFAULT_VALIDATION);
  const [picking, setPicking] = useState<{ kind: "path"; index: number } | { kind: "output" | "backup" } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = useAction(
    () => {
      const body = {
        name,
        paths: paths.filter((p) => p.trim()),
        ...storageBody(storage),
        output_path: usesOutputFolder(storage) ? outputPath || null : null,
        backup_path: usesBackupFolder(storage) ? backupPath || null : null,
        scan_interval_minutes: interval ?? 60,
        enabled,
        automation_enabled: automation,
        exclude_patterns: excludes.split("\n").map((s) => s.trim()).filter(Boolean),
        validation,
      };
      return library ? api.put<Library>(`/libraries/${library.id}`, body) : api.post<Library>("/libraries", body);
    },
    [["libraries"], ["stats"]],
  );

  const submit = () =>
    save.mutate(undefined, {
      onSuccess: (lib) => {
        toast(library ? "Library saved" : `Added ${lib.name}. Scanning now…`);
        lib.warnings?.forEach((w) => toast(w, "err"));
        onClose();
      },
      onError: (e) => setError((e as Error).message),
    });

  const setV = <K extends keyof ValidationThresholds>(k: K, v: ValidationThresholds[K]) => setValidation({ ...validation, [k]: v });

  return (
    <Modal
      wide
      title={library ? `Edit ${library.name}` : "Add library"}
      subtitle={library ? undefined : "A folder of recordings for FrameForge to watch."}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={save.isPending || !name || !paths.some((p) => p.trim())} onClick={submit}>
            {library ? "Save changes" : "Add & scan"}
          </button>
        </>
      }
    >
      <div className="stack gap-6">
        <div className="sub-section">
          <div className="form-grid">
            <Field label="Name">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="YouTube VODs" autoFocus />
            </Field>
            <Field label="Scan every" help="New and changed files are picked up on each scan">
              <NumberInput value={interval} onChange={setIntervalMin} min={5} suffix="minutes" />
            </Field>
          </div>
        </div>

        <div className="sub-section">
          <Field label="Folders" help="Paths inside the container. Sub-folders are included.">
            <div className="stack gap-2">
              {paths.map((p, i) => (
                <div className="row" key={i}>
                  <input className="input mono" value={p} aria-label={`Folder ${i + 1}`} onChange={(e) => setPaths(paths.map((x, j) => (j === i ? e.target.value : x)))} />
                  <button className="btn icon" onClick={() => setPicking({ kind: "path", index: i })} title="Browse" aria-label="Browse">
                    <FolderSearch size={16} />
                  </button>
                  {paths.length > 1 && (
                    <button className="btn icon ghost" onClick={() => setPaths(paths.filter((_, j) => j !== i))} title="Remove folder" aria-label="Remove folder">
                      <X size={16} />
                    </button>
                  )}
                </div>
              ))}
              <div>
                <button className="btn sm ghost" onClick={() => setPaths([...paths, ""])}>
                  <Plus size={14} /> Add folder
                </button>
              </div>
            </div>
          </Field>
        </div>

        <div className="sub-section">
          <div className="group-title">Output & originals</div>
          <div className="group-desc">Applies to every file this library converts.</div>
          <StorageEditor
            value={storage}
            onChange={setStorage}
            outputPath={outputPath}
            backupPath={backupPath}
            outputField={
              <Field label="Output folder" help="Sub-folders are mirrored. The scanner skips this folder if it sits inside the library.">
                <div className="row">
                  <input className="input mono" value={outputPath} onChange={(e) => setOutputPath(e.target.value)} placeholder="/media/converted" />
                  <button className="btn icon" title="Browse" aria-label="Browse" onClick={() => setPicking({ kind: "output" })}>
                    <FolderSearch size={16} />
                  </button>
                </div>
              </Field>
            }
            backupField={
              <Field label="Backup folder" help="Leave empty to use a hidden .frameforge-originals folder inside the library. A folder on another disk frees space right away.">
                <div className="row">
                  <input className="input mono" value={backupPath} onChange={(e) => setBackupPath(e.target.value)} placeholder="(inside the library)" />
                  <button className="btn icon" title="Browse" aria-label="Browse" onClick={() => setPicking({ kind: "backup" })}>
                    <FolderSearch size={16} />
                  </button>
                </div>
              </Field>
            }
          />
        </div>

        <div className="sub-section settings-list">
          <SettingRow label="Library enabled" description="Disabled libraries aren't scanned.">
            <Toggle checked={enabled} onChange={setEnabled} />
          </SettingRow>
          <SettingRow label="Create jobs automatically" description="Queue files as soon as a rule matches them. When off, each file only shows what would happen, and you can still queue files by hand.">
            <Toggle checked={automation} onChange={setAutomation} />
          </SettingRow>
        </div>

        <details className="disclosure boxed">
          <summary>Advanced: exclusions & safety checks</summary>
          <div className="settings-list">
            <SettingRow top label="Exclude patterns" description="One per line, matched against the file name or relative path, e.g. *.part or raw/*">
              <textarea className="input w-wide" rows={3} value={excludes} onChange={(e) => setExcludes(e.target.value)} aria-label="Exclude patterns" />
            </SettingRow>
            <SettingRow label="Max duration difference" description="The output must be this close to the original's length.">
              <div className="w-num">
                <NumberInput value={validation.duration_tolerance_pct} onChange={(v) => setV("duration_tolerance_pct", v ?? 2)} min={0.1} step={0.5} suffix="%" />
              </div>
            </SettingRow>
            <SettingRow label="Minimum output size">
              <div className="w-num">
                <NumberInput value={Math.round(validation.min_output_bytes / 1024 / 1024)} onChange={(v) => setV("min_output_bytes", (v ?? 1) * 1024 * 1024)} min={0} suffix="MB" />
              </div>
            </SettingRow>
            <SettingRow label="Size limit" description="Output size relative to the original. 100% means it must not be bigger. Empty disables the check.">
              <div className="w-wide-num">
                <NumberInput value={validation.max_size_ratio != null ? Math.round(validation.max_size_ratio * 100) : null} allowEmpty onChange={(v) => setV("max_size_ratio", v == null ? null : v / 100)} min={10} suffix="% of original" />
              </div>
            </SettingRow>
            <SettingRow label="Reject outputs over the size limit" description="Keep the original when the output is too big. Off: replace anyway, with a warning.">
              <Toggle checked={validation.fail_if_larger} onChange={(v) => setV("fail_if_larger", v)} />
            </SettingRow>
            <SettingRow label="Require audio when the original has audio">
              <Toggle checked={validation.require_audio_if_source_has_audio} onChange={(v) => setV("require_audio_if_source_has_audio", v)} />
            </SettingRow>
          </div>
        </details>

        {error && <Callout kind="err" title="Couldn't save">{error}</Callout>}
      </div>

      {picking && (
        <PathPicker
          initial={picking.kind === "path" ? paths[picking.index] : picking.kind === "output" ? outputPath || paths[0] : backupPath || paths[0]}
          onClose={() => setPicking(null)}
          onPick={(p) => {
            if (picking.kind === "path") setPaths(paths.map((x, j) => (j === picking.index ? p : x)));
            else if (picking.kind === "output") setOutputPath(p);
            else setBackupPath(p);
            setPicking(null);
          }}
        />
      )}
    </Modal>
  );
}
