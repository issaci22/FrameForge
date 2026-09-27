import type { ReactNode } from "react";
import { Archive, Clock, FolderOpen, FolderOutput, Trash2 } from "lucide-react";
import { RETENTION_CHOICES, RETENTION_LIMITS, storageSentence, type StorageValue } from "../lib/storage";
import { Callout, NumberInput, Segmented } from "./ui";

/**
 * A library's storage policy as two plain questions: where do converted files go, and what happens to
 * the original. The folder inputs are passed in so each page can add its own browse button and checks.
 */
export function StorageEditor({
  value,
  onChange,
  outputField,
  backupField,
  outputPath,
  backupPath,
}: {
  value: StorageValue;
  onChange: (v: StorageValue) => void;
  outputField: ReactNode;
  backupField: ReactNode;
  outputPath?: string | null;
  backupPath?: string | null;
}) {
  const set = (patch: Partial<StorageValue>) => onChange({ ...value, ...patch });
  const folder = value.output_location === "folder";
  const keeps = value.original_handling !== "delete";
  const custom = value.original_handling === "keep_days" && value.retention_days != null && !RETENTION_CHOICES.includes(value.retention_days);

  return (
    <div className="stack storage-editor">
      <div>
        <div className="tx-label">Where do converted files go?</div>
        <div className="option-cards" role="radiogroup" aria-label="Where converted files go">
          <Card on={!folder} icon={<FolderOpen size={16} />} title="Next to the original" onClick={() => set({ output_location: "source_folder" })}>
            Same folder, so it's easy to find. Names are chosen so nothing is ever overwritten.
          </Card>
          <Card on={folder} icon={<FolderOutput size={16} />} title="In a separate folder" onClick={() => set({ output_location: "folder" })}>
            Mirrors the library's sub-folders somewhere else, e.g. an archive disk or a Plex library.
          </Card>
        </div>
      </div>
      {folder && outputField}

      <div>
        <div className="tx-label">What happens to the original?</div>
        <div className="option-cards three" role="radiogroup" aria-label="What happens to the original">
          <Card on={value.original_handling === "keep"} icon={<Archive size={16} />} title="Keep it" onClick={() => set({ original_handling: "keep" })}>
            Never deleted by FrameForge. Needs space for both files.
          </Card>
          <Card on={value.original_handling === "keep_days"} icon={<Clock size={16} />} title="Keep it for a while" onClick={() => set({ original_handling: "keep_days", retention_days: value.retention_days ?? 7 })}>
            Time to check the result. Deleted after the period, once the new file passes its checks again.
          </Card>
          <Card on={value.original_handling === "delete"} icon={<Trash2 size={16} />} title="Delete after success" onClick={() => set({ original_handling: "delete" })}>
            Frees space right away, but only after the new file was verified in its final place.
          </Card>
        </div>
      </div>

      {value.original_handling === "keep_days" && (
        <div className="tx-row">
          <span className="tx-label">Keep originals for</span>
          <Segmented
            value={custom ? -1 : value.retention_days}
            onChange={(v) => set({ retention_days: v === -1 ? value.retention_days ?? 60 : v })}
            options={[...RETENTION_CHOICES.map((d) => ({ value: d as number | null, label: d === 1 ? "1 day" : `${d} days` })), { value: -1, label: "Custom" }]}
          />
          {custom && (
            <div className="w-num">
              <NumberInput value={value.retention_days} onChange={(v) => set({ retention_days: v })} min={RETENTION_LIMITS.min} max={RETENTION_LIMITS.max} suffix="days" />
            </div>
          )}
        </div>
      )}

      {!folder && keeps && (
        <div className="tx-row">
          <span className="tx-label">While it's kept</span>
          <Segmented
            value={value.kept_original_location}
            onChange={(v) => set({ kept_original_location: v })}
            options={[
              { value: "backup", label: "Move it to a backup folder" },
              { value: "in_place", label: "Leave it where it is" },
            ]}
          />
        </div>
      )}
      {!folder && keeps && value.kept_original_location === "backup" && backupField}

      <Callout kind={value.original_handling === "delete" ? "warn" : "info"} title="What will happen">
        {storageSentence(value, { outputPath, backupPath })} A failed or cancelled conversion never touches the original.
      </Callout>
    </div>
  );
}

function Card({ on, icon, title, onClick, children }: { on: boolean; icon: ReactNode; title: string; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" role="radio" aria-checked={on} className={`option-card ${on ? "on" : ""}`} onClick={onClick}>
      <span className="t">
        {icon} {title}
      </span>
      <span className="d">{children}</span>
    </button>
  );
}
