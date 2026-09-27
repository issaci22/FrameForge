import { Link } from "react-router-dom";
import { Cpu, Gauge, HardDrive, Timer, Zap } from "lucide-react";
import type { Job } from "../api/types";
import { ACTIVE_STATES } from "../api/types";
import { useLive } from "../live/events";
import { bytes, duration, timecode } from "../lib/format";
import { PriorityChip, StateChip, Timeline, Transform } from "./media";

const STAGE_TEXT: Record<string, string> = {
  assigned: "Starting on node…",
  preparing: "Checking source…",
  validating: "Validating output…",
  finalizing: "Moving file into place safely…",
};

/** One job: what it is on the left, where it stands in the middle, what you can do on the right. */
export function JobRow({ job, actions }: { job: Job; actions?: React.ReactNode }) {
  const live = useLive((s) => s.progress[job.id]);
  const active = ACTIVE_STATES.includes(job.state);
  const pct = active ? (live?.percent ?? job.progress ?? 0) : job.state === "completed" ? 100 : job.progress;
  const speed = live?.speed ?? job.speed;
  const fps = live?.fps ?? job.fps;
  const eta = live?.eta ?? job.eta_seconds;
  const elapsed = live?.elapsed;
  const barState = job.state === "failed" ? "err" : job.state === "completed" ? "ok" : job.state === "transcoding" ? "run" : active ? "wait" : undefined;

  return (
    <div className={`job-row ${active ? "is-active" : ""}`}>
      <div className="job-main">
        <Link to={`/jobs/${job.id}`} className="job-title ellipsis" title={job.source_path}>
          {job.filename}
        </Link>
        <div className="job-meta">
          <Transform from={job.source_codec} to={job.target_codec} />
          {job.resolution && <span className="mono">{job.resolution}</span>}
          <span className="ellipsis">{job.profile_name}</span>
          {job.priority !== 2 && <PriorityChip priority={job.priority} />}
        </div>
      </div>

      <div className="job-status">
        {job.state === "queued" ? (
          <div className="job-waiting ellipsis" title={job.waiting_reason ?? undefined}>
            {job.waiting_reason ?? "Waiting for a node"}
          </div>
        ) : (
          <>
            <div className="job-progress-head">
              <span className="row gap-2 min0">
                <StateChip state={job.state} />
                {active && job.state !== "transcoding" && <span className="faint small ellipsis">{STAGE_TEXT[job.state]}</span>}
                {job.state === "failed" && job.error_title && <span className="small ellipsis text-err">{job.error_title}</span>}
                {job.state === "completed" && job.source_size && job.output_size != null && (
                  <span className="small muted mono ellipsis">
                    {bytes(job.source_size)} → {bytes(job.output_size)}
                  </span>
                )}
              </span>
              {(active || job.state === "failed") && <span className="pct">{pct.toFixed(pct < 10 ? 1 : 0)}%</span>}
            </div>
            <Timeline value={pct} state={barState} />
            {active && (
              <div className="job-stats">
                <span title="Elapsed">
                  <Timer size={12} />
                  {timecode(elapsed)}
                </span>
                <span title="Time remaining">ETA {duration(eta)}</span>
                <span title="Encoding speed relative to real time">
                  <Zap size={12} />
                  {speed != null ? `${speed.toFixed(2)}×` : "—"}
                </span>
                <span title="Frames per second">
                  <Gauge size={12} />
                  {fps != null ? `${fps.toFixed(0)} fps` : "—"}
                </span>
                {live?.out_size != null && (
                  <span title="Output size so far">
                    <HardDrive size={12} />
                    {bytes(live.out_size)}
                  </span>
                )}
                <span title="Node and encoder" className="ellipsis">
                  <Cpu size={12} />
                  {job.node_name ?? "—"} · {job.encoder ?? "—"}
                  {live?.gpu_util != null && ` · GPU ${Math.round(live.gpu_util)}%`}
                </span>
              </div>
            )}
          </>
        )}
      </div>

      {actions && <div className="job-actions">{actions}</div>}
    </div>
  );
}
