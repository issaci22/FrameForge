import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Check, ChevronDown, ChevronUp, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useJob } from "../api/hooks";
import { ACTIVE_STATES } from "../api/types";
import { DiagnosisPanel } from "../components/DiagnosisPanel";
import { JobRow } from "../components/JobRow";
import { PriorityChip, StateChip } from "../components/media";
import { Callout, ErrorState, LoadingState, PageHeader, Section, Toggle } from "../components/ui";
import { logSeq, useLive } from "../live/events";
import { bytes, codec, dateTime, duration, savedRatio } from "../lib/format";
import { JobActions } from "./Queue";

const ACTION_TEXT = {
  keep: "Original is kept untouched",
  delete: "Original is deleted after the new file is verified",
  backup: "Original is moved to the backup folder after the new file is verified",
};

export function JobDetailPage() {
  const id = Number(useParams().id);
  const { data: job, error, refetch } = useJob(id);
  const active = job ? ACTIVE_STATES.includes(job.state) : false;
  const liveLog = useLive((s) => s.logs[id]);
  const liveSeq = useLive((s) => s.logSeq[id] ?? 0);
  const snapshotSeq = useRef(0);
  const { data: log } = useQuery({
    queryKey: ["job-log", id, job?.state],
    queryFn: async () => {
      const seq = logSeq(id);
      const text = await api.text(`/jobs/${id}/log?lines=3000`);
      snapshotSeq.current = seq;
      return text;
    },
    enabled: !!job,
  });
  const [follow, setFollow] = useState(true);
  const [showLog, setShowLog] = useState<boolean | null>(null);
  const logOpen = showLog ?? active;
  const logRef = useRef<HTMLPreElement>(null);
  // Live lines that arrived after the snapshot was taken.
  const newer = active && liveLog ? liveLog.slice(Math.max(0, liveLog.length - (liveSeq - snapshotSeq.current))) : [];
  const logText = newer.length ? [log ?? "", ...newer].join("\n") : log;

  useEffect(() => {
    if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logText, follow, logOpen]);

  if (error && !job)
    return (
      <div className="page">
        <PageHeader title="Job not found" back={{ to: "/queue", label: "Queue" }} />
        <ErrorState title="This job doesn't exist or couldn't be loaded" error={error} onRetry={() => refetch()} />
      </div>
    );
  if (!job) return <div className="page"><LoadingState /></div>;
  const r = savedRatio(job.source_size, job.output_size);

  return (
    <div className="page">
      <PageHeader
        back={{ to: "/queue", label: "Queue" }}
        title={job.filename}
        description={<span className="mono small break">{job.source_path}</span>}
        actions={
          <>
            {job.priority !== 2 && <PriorityChip priority={job.priority} />}
            <StateChip state={job.state} />
            <JobActions job={job} />
          </>
        }
      />

      <Section flush className="mb-section">
        <JobRow job={job} />
      </Section>

      <div className="grid-main-side">
        <div className="stack gap-6">
          {job.state === "failed" && job.diagnosis && <DiagnosisPanel diagnosis={job.diagnosis} />}
          {job.state === "queued" && job.waiting_reason && <Callout kind="info" title="Waiting">{job.waiting_reason}</Callout>}
          {job.notes.length > 0 && (
            <Callout kind="warn" title="Notes">
              <ul className="bullets">
                {job.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </Callout>
          )}

          {job.state === "completed" && (
            <Section title="Result">
              <div className="result-compare">
                <div>
                  <div className="k">Before</div>
                  <div className="v mono">{bytes(job.source_size)}</div>
                  <div className="muted small">
                    {codec(job.source_codec)} · {job.resolution}
                  </div>
                </div>
                <div>
                  <div className="k">After</div>
                  <div className="v mono text-ok">
                    {bytes(job.output_size)} {r != null && <span className="small">(−{r.toFixed(0)}%)</span>}
                  </div>
                  <div className="muted small mono ellipsis" title={job.output_path ?? ""}>
                    {job.output_path}
                  </div>
                </div>
              </div>
            </Section>
          )}

          {job.validation && (
            <Section title="Validation" actions={<span className={`chip ${job.validation.passed ? "ok" : "err"}`}>{job.validation.passed ? "Passed" : "Failed"}</span>}>
              <ul className="trace checks">
                {job.validation.checks.map((c) => (
                  <li key={c.name}>
                    {c.passed ? <Check size={14} className="ok" /> : <X size={14} className={c.severity === "warning" ? "warn" : "no"} />}
                    <span className="check-name">{c.name}</span>
                    <span className="muted">{c.detail}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Section
            title="FFmpeg log"
            description={logOpen ? undefined : "The raw encoder output, for troubleshooting."}
            actions={
              <>
                {logOpen && <Toggle checked={follow} onChange={setFollow} label="Follow" />}
                <button className="btn sm ghost" onClick={() => setShowLog(!logOpen)} aria-expanded={logOpen}>
                  {logOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />} {logOpen ? "Hide" : "Show"}
                </button>
              </>
            }
          >
            {logOpen ? (
              <pre className="log" ref={logRef}>
                {logText || "No log output yet."}
              </pre>
            ) : null}
          </Section>
        </div>

        <div className="stack gap-6">
          <Section title="Details">
            <dl className="kv">
              <dt>Profile</dt>
              <dd>{job.profile_name}</dd>
              <dt>Triggered by</dt>
              <dd>{job.manual ? "You (manual)" : job.rule_name ?? "—"}</dd>
              <dt>Node</dt>
              <dd>{job.node_id ? <Link className="link" to={`/nodes/${job.node_id}`}>{job.node_name}</Link> : "—"}</dd>
              <dt>Encoder</dt>
              <dd className="mono">{job.encoder ?? "—"}</dd>
              <dt>Decode</dt>
              <dd>{job.hw_decode == null ? "—" : job.hw_decode ? "Hardware" : "Software"}</dd>
              <dt>Duration</dt>
              <dd>{duration(job.source_duration)}</dd>
              <dt>Attempts</dt>
              <dd>{job.attempts + 1}</dd>
              <dt>Created</dt>
              <dd>{dateTime(job.created_at)}</dd>
              <dt>Started</dt>
              <dd>{dateTime(job.started_at)}</dd>
              <dt>Finished</dt>
              <dd>{dateTime(job.finished_at)}</dd>
            </dl>
          </Section>

          {job.finalize_plan && (
            <Section title="File handling" description={`${ACTION_TEXT[job.finalize_plan.original_action]}.`}>
              <dl className="kv">
                <dt>Output</dt>
                <dd className="mono small">{job.finalize_plan.final_output}</dd>
                {job.finalize_plan.backup_path && (
                  <>
                    <dt>Backup</dt>
                    <dd className="mono small">{job.finalize_plan.backup_path}</dd>
                  </>
                )}
              </dl>
            </Section>
          )}

          <Section title="History">
            <ul className="events">
              {job.events.map((e) => (
                <li key={e.id} className={e.kind === "completed" ? "done" : e.level}>
                  <span className="t">{new Date(e.at).toLocaleTimeString()}</span>
                  <span className="mark" />
                  <span>{e.message}</span>
                </li>
              ))}
            </ul>
          </Section>
        </div>
      </div>
    </div>
  );
}
