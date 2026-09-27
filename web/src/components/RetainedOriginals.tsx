import { useState } from "react";
import { Clock, ShieldAlert } from "lucide-react";
import { api } from "../api/client";
import { useAction, useRetained } from "../api/hooks";
import type { Library, RetainedOriginal } from "../api/types";
import { bytes } from "../lib/format";
import { ConfirmButton, useToast } from "./ui";

const date = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

/** Originals this library keeps for a period: when each is due, and why any are held back. */
export function RetainedOriginals({ lib }: { lib: Library }) {
  const [open, setOpen] = useState(false);
  const r = lib.retention;
  const { data: rows } = useRetained(lib.id, open);
  const applyPeriod = useAction(() => api.post(`/retention/apply-period?library_id=${lib.id}`), [["retention", lib.id], ["libraries"]]);
  const toast = useToast();
  if (!r || (r.pending === 0 && r.blocked === 0)) return null;

  return (
    <details className="disclosure retained" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <Clock size={12} /> {r.pending + r.blocked} original{r.pending + r.blocked === 1 ? "" : "s"} kept ({bytes(r.bytes)})
        {r.next_due && <span className="faint"> · next deletion {date(r.next_due)}</span>}
        {r.blocked > 0 && <span className="chip warn">{r.blocked} held back</span>}
      </summary>
      <div className="stack gap-2 mt-3">
        {lib.original_handling === "keep_days" && (
          <div className="row wrap faint small">
            <span>Changing the period never brings a deletion forward on its own.</span>
            <ConfirmButton
              className="btn sm ghost"
              confirmText="Recompute the due dates from the current period?"
              detail="Some originals may become due right away. Every safety check still runs before anything is deleted."
              confirmLabel="Recompute"
              onConfirm={() => applyPeriod.mutate(undefined, { onSuccess: () => toast("Dates recomputed from the current period") })}
            >
              Apply the current period to these
            </ConfirmButton>
          </div>
        )}
        {rows?.map((row) => (
          <Row key={row.id} row={row} libraryId={lib.id} />
        ))}
      </div>
    </details>
  );
}

function Row({ row, libraryId }: { row: RetainedOriginal; libraryId: number }) {
  const toast = useToast();
  const keep = useAction(() => api.post<RetainedOriginal>(`/retention/${row.id}/keep`), [["retention", libraryId], ["libraries"]]);
  const del = useAction(() => api.post<RetainedOriginal>(`/retention/${row.id}/delete`), [["retention", libraryId], ["libraries"]]);
  return (
    <div className={`retained-row ${row.state}`}>
      <div className="stack gap-1 min0">
        <span className="mono small ellipsis" title={row.original_path}>
          {row.original_path}
        </span>
        <span className="small faint">
          {bytes(row.original_size)} · {row.paused ? `paused: ${row.paused}` : row.state === "blocked" ? "held back" : `due ${date(row.due_at)}`}
        </span>
        {row.reason && (
          <span className={`small row gap-1 ${row.state === "blocked" ? "text-warn" : ""}`}>
            {row.state === "blocked" && <ShieldAlert size={13} />} {row.reason}
          </span>
        )}
      </div>
      <div className="row gap-2">
        <button className="btn sm" onClick={() => keep.mutate(undefined, { onSuccess: () => toast("This original will be kept") })}>
          Keep forever
        </button>
        <ConfirmButton
          className="btn sm danger"
          confirmText="Delete this original now?"
          detail="Every safety check runs first. If one fails, the original is kept and the reason is shown here."
          confirmLabel="Delete"
          onConfirm={() =>
            del.mutate(undefined, {
              onSuccess: (r) => toast(r.state === "deleted" ? "Original deleted" : `Kept: ${r.reason ?? r.state}`, r.state === "deleted" ? "ok" : "err"),
              onError: (e) => toast((e as Error).message, "err"),
            })
          }
        >
          Delete now
        </ConfirmButton>
      </div>
    </div>
  );
}
