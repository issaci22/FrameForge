import { useState } from "react";
import { RotateCcw, Search, Square, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useAction, useJobs } from "../api/hooks";
import type { Job } from "../api/types";
import { ACTIVE_STATES } from "../api/types";
import { JobRow } from "../components/JobRow";
import { ConfirmButton, Empty, ErrorState, Menu, PageHeader, Section, Spinner, Tabs, useToast } from "../components/ui";
import { PRIORITIES } from "../lib/format";

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

  const counts = data?.counts;
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
        {(group === "completed" || group === "cancelled") && (
          <ConfirmButton
            className="btn"
            confirmText={`Remove all ${group} jobs from the history?`}
            detail="Only the history entries are removed. Files are not touched."
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
