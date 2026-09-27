import { useState } from "react";
import { AlertTriangle, Check, FolderSearch, HardDrive, HelpCircle, Loader2, Plus, ShieldCheck, Trash2, X, XCircle } from "lucide-react";
import { PathPicker } from "../../components/PathPicker";
import { StorageEditor } from "../../components/StorageEditor";
import { storageTitle } from "../../lib/storage";
import { Callout, ConfirmButton, Empty, Field, NumberInput, Section, Toggle } from "../../components/ui";
import { fieldIssue, isAbsolute, newLibrary, normPath, type FolderState, type Issue, type LibraryDraft } from "./model";
import { GuideSection, issueProps, Kv, StatusChip, StepFrame, type StepProps } from "./parts";

type Pick = { key: string; target: "path" | "output" | "backup"; index: number };

const BACKUP_DIR = ".frameforge-originals";

export function StorageStep({ folders, ...p }: StepProps & { folders: Record<string, FolderState> }) {
  const { draft, update, server, issues, showErrors } = p;
  const [picking, setPicking] = useState<Pick | null>(null);
  const setLib = (key: string, patch: Partial<LibraryDraft>) => update((d) => ({ ...d, libraries: d.libraries.map((l) => (l.key === key ? { ...l, ...patch } : l)) }));
  const remove = (key: string) =>
    update((d) => ({
      ...d,
      libraries: d.libraries.filter((l) => l.key !== key),
      aging: { ...d.aging, excluded: d.aging.excluded.filter((k) => k !== key) },
      exceptions: d.exceptions.map((e) => (e.library_key === key ? { ...e, library_key: null } : e)),
    }));
  const createdIds = new Set(Object.entries(draft.committed).filter(([k]) => k.startsWith("library:")).map(([, v]) => v));
  const preexisting = (server.libraries ?? []).filter((l) => !createdIds.has(l.id));
  const pickLib = picking ? draft.libraries.find((l) => l.key === picking.key) : undefined;

  return (
    <StepFrame
      step="storage"
      title="Add your media libraries"
      lede="A library is a set of folders FrameForge watches, plus what happens to an original once its converted copy checks out. Split raw recordings, VODs and archives into separate libraries when they need different treatment."
      guide={<StorageGuide />}
    >
      {preexisting.length > 0 && (
        <Callout kind="info" title={`This server already has ${preexisting.length} ${preexisting.length === 1 ? "library" : "libraries"}`}>
          {preexisting.map((l) => l.name).join(", ")}. Libraries added here are created alongside them.
        </Callout>
      )}

      {draft.libraries.map((lib, i) =>
        `library:${lib.key}` in draft.committed ? (
          <CreatedLibrary key={lib.key} lib={lib} index={i} />
        ) : (
          <LibraryPanel
            key={lib.key}
            lib={lib}
            index={i}
            issues={issues.filter((x) => x.key === lib.key)}
            showErrors={showErrors}
            folders={folders}
            onChange={(patch) => setLib(lib.key, patch)}
            onRemove={() => remove(lib.key)}
            onPick={(target, index) => setPicking({ key: lib.key, target, index })}
          />
        ),
      )}

      {draft.libraries.length === 0 && (
        <Section>
          <Empty compact icon={<HardDrive size={20} />} title="No libraries yet">
            FrameForge won't scan anything until you add one, here or later on the Libraries page.
          </Empty>
        </Section>
      )}

      <div className="row">
        <button type="button" className="btn" onClick={() => update((d) => ({ ...d, libraries: [...d.libraries, newLibrary(d.libraries.length === 0)] }))}>
          <Plus size={14} /> Add library
        </button>
        <span className="faint small">Each library gets its own output policy and safety checks.</span>
      </div>

      {picking && pickLib && (
        <PathPicker
          initial={picking.target === "path" ? pickLib.paths[picking.index] : picking.target === "output" ? pickLib.output_path || pickLib.paths[0] : pickLib.backup_path || pickLib.paths[0]}
          onClose={() => setPicking(null)}
          onPick={(path) => {
            if (picking.target === "path") setLib(pickLib.key, { paths: pickLib.paths.map((x, j) => (j === picking.index ? path : x)) });
            else if (picking.target === "output") setLib(pickLib.key, { output_path: path });
            else setLib(pickLib.key, { backup_path: path });
            setPicking(null);
          }}
        />
      )}
    </StepFrame>
  );
}

function LibraryPanel({
  lib,
  index,
  issues,
  showErrors,
  folders,
  onChange,
  onRemove,
  onPick,
}: {
  lib: LibraryDraft;
  index: number;
  issues: Issue[];
  showErrors: boolean;
  folders: Record<string, FolderState>;
  onChange: (patch: Partial<LibraryDraft>) => void;
  onRemove: () => void;
  onPick: (target: Pick["target"], index: number) => void;
}) {
  const id = (s: string) => `${lib.key}-${s}`;
  const v = lib.validation;
  const setV = (patch: Partial<LibraryDraft["validation"]>) => onChange({ validation: { ...v, ...patch } });
  const firstPath = lib.paths.find((x) => isAbsolute(x));
  const noPaths = fieldIssue(issues, "path:0");

  return (
    <section className="panel lib-panel" aria-labelledby={id("title")}>
      <div className="panel-head">
        <span className="mono faint">{String(index + 1).padStart(2, "0")}</span>
        <h3 id={id("title")}>
          {lib.name.trim() || "New library"}
        </h3>
        <span className="chip outline">{storageTitle(lib.storage)}</span>
        <div className="right">
          <ConfirmButton className="btn sm ghost danger" confirmText="Remove from setup?" confirmLabel="Remove" onConfirm={onRemove}>
            <Trash2 size={12} /> Remove
          </ConfirmButton>
        </div>
      </div>
      <div className="panel-body stack">
        <div className="form-grid">
          <Field label="Library name" htmlFor={id("name")} {...issueProps(issues, "name", showErrors)}>
            <input id={id("name")} className="input" value={lib.name} placeholder="Stream VODs" onChange={(e) => onChange({ name: e.target.value })} />
          </Field>
          <Field label="Scan every" help="New and changed files are found on each scan" {...issueProps(issues, "interval", showErrors)}>
            <NumberInput value={lib.scan_interval_minutes} onChange={(x) => onChange({ scan_interval_minutes: x })} min={5} max={10080} suffix="minutes" allowEmpty />
          </Field>
        </div>

        <Field label="Folders inside the container" help="The path as the FrameForge container sees it, not the host path. Sub-folders are included." error={showErrors && noPaths?.text === "Add at least one folder." ? noPaths.text : null}>
          <div className="stack gap-2">
            {lib.paths.map((path, i) => (
              <div key={i} className="path-entry">
                <div className="row">
                  <input
                    className="input mono"
                    aria-label={`Folder ${i + 1}`}
                    value={path}
                    onChange={(e) => onChange({ paths: lib.paths.map((x, j) => (j === i ? e.target.value : x)) })}
                    aria-invalid={fieldIssue(issues, `path:${i}`)?.level === "error" || undefined}
                  />
                  <button className="btn icon" type="button" title="Browse folders" aria-label={`Browse for folder ${i + 1}`} onClick={() => onPick("path", i)}>
                    <FolderSearch size={14} />
                  </button>
                  {lib.paths.length > 1 && (
                    <button className="btn icon ghost" type="button" aria-label={`Remove folder ${i + 1}`} title="Remove folder" onClick={() => onChange({ paths: lib.paths.filter((_, j) => j !== i) })}>
                      <X size={14} />
                    </button>
                  )}
                </div>
                <PathStatus path={path} state={folders[normPath(path)]} issue={fieldIssue(issues, `path:${i}`)} mustExist />
              </div>
            ))}
            <div>
              <button className="btn sm ghost" type="button" onClick={() => onChange({ paths: [...lib.paths, "/media/"] })}>
                <Plus size={12} /> Add folder
              </button>
            </div>
          </div>
        </Field>

        <StorageEditor
          value={lib.storage}
          onChange={(storage) => onChange({ storage })}
          outputPath={lib.output_path}
          backupPath={lib.backup_path}
          outputField={
            <Field label="Output folder" htmlFor={id("output")} help="Sub-folders are mirrored. The scanner skips this folder if it sits inside the library." {...issueProps(issues, "output_path", showErrors)}>
              <div className="row">
                <input id={id("output")} className="input mono" value={lib.output_path} placeholder="/media/converted" onChange={(e) => onChange({ output_path: e.target.value })} />
                <button className="btn icon" type="button" aria-label="Browse for the output folder" title="Browse folders" onClick={() => onPick("output", 0)}>
                  <FolderSearch size={14} />
                </button>
              </div>
              <PathStatus path={lib.output_path} state={folders[normPath(lib.output_path)]} />
            </Field>
          }
          backupField={
            <Field label="Backup folder" htmlFor={id("backup")} {...issueProps(issues, "backup_path", showErrors)}>
              <div className="row">
                <input id={id("backup")} className="input mono" value={lib.backup_path} placeholder={`(empty: ${BACKUP_DIR} inside each library folder)`} onChange={(e) => onChange({ backup_path: e.target.value })} />
                <button className="btn icon" type="button" aria-label="Browse for the backup folder" title="Browse folders" onClick={() => onPick("backup", 0)}>
                  <FolderSearch size={14} />
                </button>
              </div>
              {lib.backup_path.trim() ? (
                <PathStatus path={lib.backup_path} state={folders[normPath(lib.backup_path)]} />
              ) : (
                <div className="help">
                  Originals go to <span className="mono">{firstPath ? `${normPath(firstPath)}/${BACKUP_DIR}` : BACKUP_DIR}</span>. That's the same disk, so no space is freed until backups are deleted (by you, or by a retention period). A folder on another disk frees space right away (cross-disk moves are copied and verified first).
                </div>
              )}
            </Field>
          }
        />
        {showErrors && fieldIssue(issues, "retention") && <Callout kind="err">{fieldIssue(issues, "retention")!.text}</Callout>}

        <div className="safety" role="group" aria-labelledby={id("safety")}>
          <div className="sec-label" id={id("safety")}>
            <ShieldCheck size={13} /> Safety checks
            <span className="faint sec-label-note">
              Every output must pass these before the original is touched
            </span>
          </div>
          <div className="safety-grid">
            <div className="safety-size">
              <Field label="Size limit" {...issueProps(issues, "size", showErrors)}>
                <div className="w-wide-num">
                  <NumberInput value={v.max_size_ratio != null ? Math.round(v.max_size_ratio * 100) : null} allowEmpty onChange={(x) => setV({ max_size_ratio: x == null ? null : x / 100 })} min={10} suffix="% of original" placeholder="off" />
                </div>
              </Field>
              <Toggle checked={v.fail_if_larger} disabled={v.max_size_ratio == null} onChange={(x) => setV({ fail_if_larger: x })} label="Reject outputs over the size limit and keep the original" />
              <p className="faint small">
                At the default 100%, a new file that is bigger than the original is discarded: it would cost space instead of saving it. That happens with low-bitrate or already-efficient sources (H.265, AV1) and with VA-API's constant-QP mode. When this is off, the larger file is still used, with a warning. Remuxes skip the size check.
              </p>
            </div>
            <div className="stack gap-3">
              <Field label="Max duration difference" help="Compared with the original's length" {...issueProps(issues, "duration", showErrors)}>
                <NumberInput value={v.duration_tolerance_pct} onChange={(x) => setV({ duration_tolerance_pct: x ?? 0 })} min={0.1} step={0.5} suffix="%" />
              </Field>
              <Field label="Minimum output size" help="Anything smaller is treated as broken" {...issueProps(issues, "min_size", showErrors)}>
                <NumberInput value={Math.round(v.min_output_bytes / 1024 / 1024)} onChange={(x) => setV({ min_output_bytes: (x ?? 0) * 1024 * 1024 })} min={0} suffix="MB" />
              </Field>
              <Toggle checked={v.require_audio_if_source_has_audio} onChange={(x) => setV({ require_audio_if_source_has_audio: x })} label="Require audio when the original has audio" />
            </div>
          </div>
        </div>

        <details className="disclosure">
          <summary>Advanced: exclude patterns</summary>
          <div className="mt-2">
            <Field label="Exclude patterns" htmlFor={id("excludes")} help="One per line, shell-style, matched against the file name and the path inside the library, e.g. replay-*, clips/*, *.part.mkv">
              <textarea id={id("excludes")} className="input" rows={3} value={lib.exclude_patterns} onChange={(e) => onChange({ exclude_patterns: e.target.value })} />
            </Field>
          </div>
        </details>
      </div>
    </section>
  );
}

function PathStatus({ path, state, issue, mustExist }: { path: string; state?: FolderState; issue?: Issue; mustExist?: boolean }) {
  if (!path.trim()) return null;
  if (!isAbsolute(path)) return <div className="path-status err"><XCircle size={12} /> Use an absolute path inside the container, e.g. /media/vods.</div>;
  const extra = issue?.level === "warn" && !issue.text.startsWith("The container can't") ? <div className="path-status warn"><AlertTriangle size={12} /> {issue.text}</div> : null;
  let line;
  switch (state?.state) {
    case "found":
      line = <div className="path-status ok"><Check size={12} /> Found · {state.dirs} sub-folder{state.dirs === 1 ? "" : "s"}</div>;
      break;
    case "missing":
      line = mustExist ? (
        <div className="path-status err"><XCircle size={12} /> Not found inside the container. Check the volume mounts in docker-compose.yml.</div>
      ) : (
        <div className="path-status"><HelpCircle size={12} /> Doesn't exist yet. It's created when the first file needs it.</div>
      );
      break;
    case "denied":
      line = <div className="path-status warn"><AlertTriangle size={12} /> The container can't list this folder (permission denied). Check PUID/PGID.</div>;
      break;
    case "unknown":
      line = <div className="path-status"><HelpCircle size={12} /> Couldn't check: {state.message}</div>;
      break;
    default:
      line = <div className="path-status"><Loader2 size={12} className="spin" /> Checking…</div>;
  }
  return (
    <>
      {line}
      {extra}
    </>
  );
}

function CreatedLibrary({ lib, index }: { lib: LibraryDraft; index: number }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <span className="mono faint">{String(index + 1).padStart(2, "0")}</span>
        <h3>{lib.name}</h3>
        <div className="right">
          <StatusChip tone="ok">Created</StatusChip>
        </div>
      </div>
      <div className="panel-body stack gap-2">
        <Kv
          rows={[
            ["Folders", <span className="mono">{lib.paths.join(", ")}</span>],
            ["After converting", storageTitle(lib.storage)],
          ]}
        />
        <div className="faint small">Already saved on the server. Change it later on the Libraries page.</div>
      </div>
    </section>
  );
}

function StorageGuide() {
  return (
    <>
      <GuideSection title="Which path goes where">
        <div className="path-model">
          <div className="hop">
            <div className="k">Host folder</div>
            <div className="v">/mnt/nas/vods</div>
          </div>
          <div className="link">volume in docker-compose.yml</div>
          <div className="hop on">
            <div className="k">FrameForge path · enter this</div>
            <div className="v">/media/vods</div>
          </div>
          <div className="link">path mapping on the node (Nodes step)</div>
          <div className="hop">
            <div className="k">Remote node path</div>
            <div className="v">/mnt/vods</div>
          </div>
        </div>
        <div>Libraries use the path as the FrameForge container sees it. Remote nodes read the same storage; a mapping only translates the prefix. Files are never copied over the network.</div>
      </GuideSection>
      <GuideSection title="How a file is replaced">
        <ol>
          <li>FFmpeg writes into a hidden .frameforge-tmp folder next to the destination, never to the final path.</li>
          <li>The output is validated: the checks here, plus ffprobe can read it, it has a video stream, and its first and last 5 seconds decode.</li>
          <li>It receives the original's modification time, permissions and owner, so age rules keep counting.</li>
          <li>It's moved into place, every step journaled, and probed again there.</li>
          <li>Only then is the original backed up or deleted, as the policy says.</li>
        </ol>
        <div>A failed or cancelled job leaves the original byte-for-byte untouched, and FrameForge never overwrites a file it didn't create.</div>
      </GuideSection>
      <GuideSection title="Recognizing files">
        <div>Each file is fingerprinted with xxHash3-128 over its size and three 1 MiB samples (start, middle, end). That's fast on multi-GB recordings and recognizes moved or renamed files. It identifies files; it is not a full-file or cryptographic checksum.</div>
      </GuideSection>
    </>
  );
}
