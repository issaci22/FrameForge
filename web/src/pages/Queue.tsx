import { useState } from "react";
import { ListX, RotateCcw, Search, Square, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useAction, useJobs } from "../api/hooks";
import type { ClearQueueResult, Job } from "../api/types";
import { ACTIVE_STATES } from "../api/types";
import { JobRow } from "../components/JobRow";
import { ConfirmButton, Empty, ErrorState, Menu, PageHeader, Section, Spinner, Tabs, Toggle, useToast } from "../components/ui";
import { plural, PRIORITIES } from "../lib/format";

type Group = "active" | "queued" | "completed" | "failed" | "cancelled";
const PAGE = 50;

const EMPTY_TEXT: Record<Group, { title: string; body: string }> = {
  active: { title: "Nothing is running", body: "Jobs appear here while a node works on them." },
  queued: { title: "The queue is empty", body: "Files are queued when a rule matches them during a scan, or when you convert one by hand from Files." },
  completed: { title: "No completed jobs", body: "Converted files show up here with their before and after sizes." },
  failed: { title: "No failed jobs", body: "When a conversion fails, it is listed here with an explanation. The original is never touched." },
  cancelled: { title: "No cancelled jobs", body: "Jobs you stop show up here and can be retried." },
};

export function QueuePage() {
  const [group, setGroup] = useState<Group>("active");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, refetch } = useJobs({ group, q: q || undefined, limit: PAGE, offset });
  const toast = useToast();
  const clear = useAction((states: string[]) => api.post<{ removed: number }>("/jobs/clear", { states }), [["jobs"], ["stats"]]);
  // Lives here, not in the button: the button disappears as soon as the queue is empty, and the result still needs a toast.
  const clearQueue = useAction((stop: boolean) => api.post<ClearQueueResult>("/jobs/clear-queue", { stop_running: stop }), [["jobs"], ["stats"], ["files"]]);

  const counts = data?.counts;
  const queued = counts?.queued ?? 0;
  const running = counts?.active ?? 0;
  const switchTo = (g: Group) => {
    setGroup(g);
    setOffset(0);
  };

  return (
    <div className="page">
      <PageHeader title="Queue" description="Jobs run highest priority first. Within a priority, oldest first." />

      <div className="toolbar">
        <Tabs
          variant="pills"
          value={group}
          onChange={switchTo}
          tabs={[
            { value: "active", label: "Running", count: counts?.active },
            { value: "queued", label: "Queued", count: counts?.queued },
            { value: "completed", label: "Completed", count: counts?.completed },
            { value: "failed", label: "Failed", count: counts?.failed },
            { value: "cancelled", label: "Cancelled", count: counts?.cancelled },
          ]}
        />
        <span className="spacer" />
        <label className="input-icon search">
          <Search size={15} />
          <input className="input" placeholder="Filter by file name" aria-label="Filter by file name" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        {(group === "active" || group === "queued") && queued > 0 && (
          <ClearQueueButton
            queued={queued}
            running={running}
            pending={clearQueue.isPending}
            onClear={(stop) => clearQueue.mutate(stop, { onSuccess: (r) => toast(clearedText(r)), onError: (e) => toast((e as Error).message, "err") })}
          />
        )}
        {(group === "completed" || group === "cancelled") && (
          <ConfirmButton
            className="btn"
            confirmText={`Remove all ${group} jobs from the history?`}
            detail={
              group === "cancelled"
                ? "Only the history entries are removed. Files are not touched. Files you cancelled can be queued again by later scans."
                : "Only the history entries are removed. Files are not touched."
            }
            confirmLabel="Remove"
            onConfirm={() =>
              clear.mutate([group], {
                onSuccess: (r) => toast(`Removed ${r.removed} jobs from history`),
              })
            }
          >
            <Trash2 size={14} /> Clear {group}
          </ConfirmButton>
        )}
      </div>

      <Section flush>
        {error && !data ? (
          <ErrorState title="Couldn't load jobs" error={error} onRetry={() => refetch()} />
        ) : !data ? (
          <Spinner />
        ) : data.items.length ? (
          <div className="job-list">
            {data.items.map((j) => (
              <JobRow key={j.id} job={j} actions={<JobActions job={j} />} />
            ))}
          </div>
        ) : (
          <Empty title={q ? "No jobs match" : EMPTY_TEXT[group].title}>{q ? "Try a different file name." : EMPTY_TEXT[group].body}</Empty>
        )}
      </Section>

      {data && data.total > PAGE && (
        <div className="pager">
          <span className="faint small">
            {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
          </span>
          <span className="spacer" />
          <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            Previous
          </button>
          <button className="btn sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function clearedText(r: ClearQueueResult): string {
  const parts = [r.cancelled ? `Cancelled ${plural(r.cancelled, "pending job")}.` : "Nothing was waiting in the queue."];
  if (r.stopping) parts.push(`Stopping ${plural(r.stopping, "running job")}…`);
  if (r.stopping_offline) parts.push(`${plural(r.stopping_offline, "job")} on an offline node will stop when it reconnects.`);
  if (r.still_running) parts.push(`${plural(r.still_running, "job is", "jobs are")} still running.`);
  return parts.join(" ");
}

/** Cancel every pending job in one go; optionally ask nodes to stop the running ones too. */
function ClearQueueButton({ queued, running, pending, onClear }: { queued: number; running: number; pending: boolean; onClear: (stopRunning: boolean) => void }) {
  const [stopRunning, setStopRunning] = useState(false);
  const stopping = stopRunning && running > 0;
  return (
    <ConfirmButton
      className="btn danger"
      disabled={pending}
      confirmText={`Cancel all ${plural(queued, "pending job")}?`}
      detail={
        stopping
          ? "They move to Cancelled and aren't queued again automatically; retry them any time. Stopped jobs delete their partial output and leave the original untouched. Jobs already moving files into place finish normally."
          : `They move to Cancelled and aren't queued again automatically; retry them any time. Files aren't touched.${running ? " Running jobs keep running." : ""}`
      }
      extra={running > 0 ? <Toggle checked={stopRunning} onChange={setStopRunning} label={`Also stop ${plural(running, "running job")}`} /> : undefined}
      confirmLabel={stopping ? "Clear and stop" : "Clear queue"}
      onConfirm={() => {
        onClear(stopping);
        setStopRunning(false); // off by default every time
      }}
    >
      <ListX size={14} /> Clear queue
    </ConfirmButton>
  );
}

export function JobActions({ job }: { job: Job }) {
  const toast = useToast();
  const cancel = useAction(() => api.post(`/jobs/${job.id}/cancel`), [["jobs"]]);
  const retry = useAction(() => api.post(`/jobs/${job.id}/retry`), [["jobs"]]);
  const prio = useAction((p: number) => api.post(`/jobs/${job.id}/priority`, { priority: p }), [["jobs"]]);
  const onErr = (e: Error) => toast(e.message, "err");

  if (job.state === "queued") {
    return (
      <>
        <select className="select sm prio-select" value={job.priority} onChange={(e) => prio.mutate(Number(e.target.value), { onError: onErr })} aria-label="Priority" title="Priority">
          {PRIORITIES.map((p, i) => (
            <option key={p} value={i}>
              {p}
            </option>
          ))}
        </select>
        <Menu
          label="Job actions"
          items={[
            {
              label: "Cancel job",
              icon: <Square size={14} />,
              danger: true,
              confirm: { text: "Cancel this job?", detail: "It moves to Cancelled, where you can retry it. The file isn't touched.", label: "Cancel job" },
              onSelect: () => cancel.mutate(undefined, { onError: onErr }),
            },
          ]}
        />
      </>
    );
  }
  if (ACTIVE_STATES.includes(job.state)) {
    if (job.cancel_requested && job.state !== "finalizing") {
      return (
        <span className="faint small" title="Waiting for the node to confirm. The job moves to Cancelled once it has stopped.">
          Stopping…
        </span>
      );
    }
    return job.state === "finalizing" ? (
      <span className="faint small" title="Cancelling isn't possible while files are being moved">
        Finishing…
      </span>
    ) : (
      <ConfirmButton
        className="btn sm danger"
        confirmText="Stop this job?"
        detail="The partial output is deleted. The original stays untouched."
        confirmLabel="Stop job"
        onConfirm={() => cancel.mutate(undefined, { onSuccess: () => toast("Stopping job…"), onError: onErr })}
      >
        <Square size={12} /> Stop
      </ConfirmButton>
    );
  }
  if (job.state === "failed" || job.state === "cancelled") {
    return (
      <button className="btn sm" onClick={() => retry.mutate(undefined, { onSuccess: () => toast("Queued again"), onError: onErr })}>
        <RotateCcw size={13} /> Retry
      </button>
    );
  }
  return null;
}
