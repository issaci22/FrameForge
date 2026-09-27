import { Link } from "react-router-dom";
import { ArrowRight, CheckCircle2, Clock, FolderPlus, HardDrive, Loader, Server } from "lucide-react";
import { useHistory, useJobs, useLibraries, useNodes, useOverview } from "../api/hooks";
import type { Node } from "../api/types";
import { JobRow } from "../components/JobRow";
import { Meter, StateChip, Transform } from "../components/media";
import { Empty, ErrorState, LoadingState, PageHeader, Section } from "../components/ui";
import { useLive } from "../live/events";
import { ago, bytes, duration, rate, savedRatio } from "../lib/format";

export function DashboardPage() {
  const { data: o, error, refetch } = useOverview();
  const { data: active } = useJobs({ group: "active", limit: 20 });
  const { data: queued } = useJobs({ group: "queued", limit: 5 });
  const { data: recent } = useJobs({ group: "finished", limit: 8 });
  const { data: libraries } = useLibraries();
  const { data: nodes } = useNodes();
  const liveCount = useLive((s) => Object.keys(s.progress).length);

  if (error && !o) return <div className="page"><ErrorState title="Couldn't load the dashboard" error={error} onRetry={() => refetch()} /></div>;
  if (!o) return <div className="page"><LoadingState /></div>;

  const activeCount = Math.max(o.jobs.active, liveCount);
  const noLibraries = libraries?.length === 0;

  return (
    <div className="page">
      <PageHeader title="Dashboard" description="What FrameForge is working on, and how much space it has saved." />

      <div className="stat-grid">
        <Stat icon={<Loader size={16} />} label="Transcoding" value={activeCount} accent={activeCount > 0} hint={`${o.nodes.online} of ${o.nodes.total} nodes online`} />
        <Stat
          icon={<Clock size={16} />}
          label="Queued"
          value={o.jobs.queued}
          hint={activeCount || o.jobs.queued ? (o.eta_seconds != null ? `About ${duration(o.eta_seconds)} left` : "Estimating time left") : "Nothing waiting"}
        />
        <Stat
          icon={<CheckCircle2 size={16} />}
          label="Completed today"
          value={o.jobs.completed_today}
          hint={o.jobs.failed_today ? <span className="text-err">{o.jobs.failed_today} failed</span> : "No failures"}
        />
        <Stat icon={<HardDrive size={16} />} label="Space saved" value={bytes(o.storage.saved)} hint={`${o.jobs.completed_total} files converted · ${rate(o.throughput_bps)} now`} />
      </div>

      {noLibraries && (
        <Section>
          <Empty
            icon={<FolderPlus size={20} />}
            title="Add your first library"
            action={
              <Link to="/libraries" className="btn primary">
                Go to Libraries
              </Link>
            }
          >
            Point FrameForge at a folder of recordings. It analyzes every video first and changes nothing until a rule tells it to.
          </Empty>
        </Section>
      )}

      <div className="grid-main-side">
        <div className="stack gap-6">
          <Section
            title="Now transcoding"
            flush
            actions={
              <Link to="/queue" className="btn sm ghost">
                Open queue <ArrowRight size={14} />
              </Link>
            }
          >
            {active?.items.length ? (
              <div className="job-list">
                {active.items.map((j) => (
                  <JobRow key={j.id} job={j} />
                ))}
              </div>
            ) : (
              <Empty compact title={o.jobs.queued ? "Jobs are waiting" : "Nothing running"}>
                {o.jobs.queued
                  ? queued?.items[0]?.waiting_reason ?? "Waiting for a free node."
                  : libraries?.length
                    ? "Files appear here when a rule matches them. Rules are checked every time a library is scanned."
                    : "Add a library to get started."}
              </Empty>
            )}
          </Section>

          {queued && queued.items.length > 0 && (
            <Section title="Up next" description={`${o.jobs.queued} in the queue`} flush>
              <div className="job-list">
                {queued.items.map((j) => (
                  <JobRow key={j.id} job={j} />
                ))}
              </div>
            </Section>
          )}

          <Section title="Recently finished" flush>
            {recent?.items.length ? (
              <ul className="list-plain recent-list">
                {recent.items.map((j) => {
                  const r = savedRatio(j.source_size, j.output_size);
                  return (
                    <li key={j.id}>
                      <StateChip state={j.state} />
                      <div className="min0 flex-1">
                        <Link to={`/jobs/${j.id}`} className="ellipsis block strong">
                          {j.filename}
                        </Link>
                        {j.state === "failed" && j.error_title && <div className="small text-err ellipsis">{j.error_title}</div>}
                      </div>
                      <Transform from={j.source_codec} to={j.target_codec} />
                      <span className="saved mono">{j.state === "completed" && r != null ? `−${r.toFixed(0)}%` : ""}</span>
                      <span className="when faint small">{ago(j.finished_at)}</span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <Empty compact title="No history yet">Finished and failed jobs show up here.</Empty>
            )}
          </Section>
        </div>

        <div className="stack gap-6">
          <Section
            title="Nodes"
            flush
            actions={
              <Link to="/nodes" className="btn sm ghost">
                Manage
              </Link>
            }
          >
            {nodes?.length ? (
              <div className="node-mini-list">
                {nodes.map((n) => (
                  <NodeMini key={n.id} node={n} />
                ))}
              </div>
            ) : (
              <Empty compact icon={<Server size={18} />} title="No nodes" />
            )}
          </Section>

          <LibraryStorage storage={o.storage} />
          <SavingsChart />
        </div>
      </div>
    </div>
  );
}

function Stat({ icon, label, value, hint, accent }: { icon: React.ReactNode; label: string; value: React.ReactNode; hint?: React.ReactNode; accent?: boolean }) {
  return (
    <div className={`stat ${accent ? "accent" : ""}`}>
      <div className="label">
        {icon}
        {label}
      </div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

function LibraryStorage({ storage: lib }: { storage: { library_original: number; library_current: number; library_files: number } }) {
  const saved = lib.library_original - lib.library_current;
  const pct = lib.library_original ? (saved / lib.library_original) * 100 : 0;
  return (
    <Section title="Library storage">
      {lib.library_files > 0 ? (
        <div className="stack gap-3">
          <div className="row baseline">
            <span className="big-num">{bytes(lib.library_current)}</span>
            <span className="faint small">now, from {bytes(lib.library_original)}</span>
          </div>
          <div className="timeline">
            <div className="fill" style={{ width: `${lib.library_original ? (lib.library_current / lib.library_original) * 100 : 100}%` }} />
          </div>
          <div className="row small">
            <span className="muted">{lib.library_files} files</span>
            <span className="spacer" />
            <span className="text-ok mono">
              −{bytes(saved)} ({pct.toFixed(0)}%)
            </span>
          </div>
        </div>
      ) : (
        <span className="muted small">No files scanned yet.</span>
      )}
    </Section>
  );
}

function NodeMini({ node }: { node: Node }) {
  const live = useLive((s) => s.metrics[node.id]);
  const m = live?.metrics ?? node.metrics;
  const gpu = m?.gpus?.[0];
  const active = live?.active ?? node.active_jobs;
  const hw = node.capabilities?.encoders.filter((e) => e.verified && e.backend !== "cpu").map((e) => e.backend);
  const hwLabel = hw && hw.length ? [...new Set(hw)].map((b) => b.toUpperCase()).join(" · ") : "CPU only";
  return (
    <Link to={`/nodes/${node.id}`} className="node-mini">
      <div className="row">
        <span className={`status-dot ${node.status}`} />
        <span className="ellipsis strong flex-1">{node.name}</span>
        <span className="faint small mono" title="Jobs running / slots">
          {active.length}/{node.max_concurrency}
        </span>
      </div>
      {node.online && m ? (
        <div className="stack gap-2">
          <Meter name="CPU" value={m.cpu_percent} />
          {gpu && <Meter name="GPU" value={gpu.utilization} />}
          <Meter name="RAM" value={(m.ram_used_mb / m.ram_total_mb) * 100} label={`${(m.ram_used_mb / 1024).toFixed(1)} GB`} />
        </div>
      ) : (
        <span className="faint small">{node.status === "pending" ? "Waiting for first connection" : `Offline · last seen ${ago(node.last_seen_at)}`}</span>
      )}
      <span className="faint caption">{hwLabel}</span>
    </Link>
  );
}

function SavingsChart() {
  const { data } = useHistory(30);
  if (!data) return null;
  const max = Math.max(1, ...data.map((d) => d.saved));
  const total = data.reduce((a, d) => a + d.saved, 0);
  return (
    <Section title="Saved in the last 30 days" actions={<span className="mono small muted">{bytes(total)}</span>}>
      <div className="bars" role="img" aria-label={`${bytes(total)} saved in the last 30 days`}>
        {data.map((d) => (
          <div
            key={d.day}
            className={`b ${d.saved <= 0 ? "zero" : ""}`}
            style={{ height: `${Math.max(2, (Math.max(0, d.saved) / max) * 100)}%` }}
            title={`${d.day}: ${bytes(d.saved)} saved, ${d.completed} converted${d.failed ? `, ${d.failed} failed` : ""}`}
          />
        ))}
      </div>
      <div className="bars-axis">
        <span>{data[0]?.day.slice(5)}</span>
        <span>today</span>
      </div>
    </Section>
  );
}
