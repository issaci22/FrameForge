import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Check, Minus, RefreshCw, Search, X } from "lucide-react";
import { api } from "../api/client";
import { useAction, useFile, useFiles, useLibraries, useProfiles } from "../api/hooks";
import type { FileStatus, MediaFile } from "../api/types";
import { CodecChip, StateChip } from "../components/media";
import { Callout, Drawer, Empty, ErrorState, Field, PageHeader, Section, Spinner, Toggle, useToast } from "../components/ui";
import { ageDays, bytes, date, duration, PRIORITIES } from "../lib/format";

const STATUS_LABEL: Record<FileStatus, { text: string; cls: string }> = {
  new: { text: "Analyzing", cls: "" },
  ready: { text: "Analyzed", cls: "" },
  queued: { text: "Queued", cls: "accent" },
  processing: { text: "Processing", cls: "accent" },
  processed: { text: "Converted", cls: "ok" },
  failed: { text: "Failed", cls: "err" },
  error: { text: "Unreadable", cls: "err" },
  missing: { text: "Missing", cls: "warn" },
};

const PAGE = 100;

export function FilesPage() {
  const [params, setParams] = useSearchParams();
  const libraryId = params.get("library") ? Number(params.get("library")) : undefined;
  const status = params.get("status") ?? "";
  const codec = params.get("codec") ?? "";
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("modified");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const { data: libraries } = useLibraries();
  const { data, error, refetch } = useFiles({ library_id: libraryId, status: status || undefined, codec: codec || undefined, q: q || undefined, sort, order: sort === "name" ? "asc" : "desc", offset, limit: PAGE });
  const filtered = !!(libraryId || status || codec || q);

  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next);
    setOffset(0);
  };

  return (
    <div className="page">
      <PageHeader title="Files" description="Everything FrameForge found, and what it plans to do with each file. Select a file for details." />

      <div className="toolbar">
        <label className="input-icon search grow">
          <Search size={15} />
          <input
            className="input"
            placeholder="Search file names"
            aria-label="Search file names"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        <select className="select w-select" aria-label="Library" value={libraryId ?? ""} onChange={(e) => setParam("library", e.target.value)}>
          <option value="">All libraries</option>
          {libraries?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
        <select className="select w-select-sm" aria-label="Status" value={status} onChange={(e) => setParam("status", e.target.value)}>
          <option value="">Any status</option>
          {Object.entries(STATUS_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v.text}
            </option>
          ))}
        </select>
        <select className="select w-select-sm" aria-label="Codec" value={codec} onChange={(e) => setParam("codec", e.target.value)}>
          <option value="">Any codec</option>
          <option value="h264">H.264</option>
          <option value="hevc">H.265</option>
          <option value="av1">AV1</option>
          <option value="vp9">VP9</option>
          <option value="prores">ProRes</option>
        </select>
        <select className="select w-select-sm" aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="modified">Newest first</option>
          <option value="size">Largest first</option>
          <option value="name">Name</option>
        </select>
      </div>

      <Section flush>
        {error && !data ? (
          <ErrorState title="Couldn't load files" error={error} onRetry={() => refetch()} />
        ) : !data ? (
          <Spinner />
        ) : data.items.length ? (
          <div className="table-scroll">
            <table className="table files-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Video</th>
                  <th>Length</th>
                  <th className="right">Size</th>
                  <th>Age</th>
                  <th>Status</th>
                  <th>Plan</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((f) => (
                  <FileRow key={f.id} f={f} selected={selected === f.id} onClick={() => setSelected(f.id)} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="No files match"
            action={
              filtered ? (
                <button
                  className="btn"
                  onClick={() => {
                    setQ("");
                    setParams(new URLSearchParams());
                    setOffset(0);
                  }}
                >
                  Clear filters
                </button>
              ) : libraries?.length === 0 ? (
                <Link to="/libraries" className="btn primary">
                  Add a library
                </Link>
              ) : undefined
            }
          >
            {filtered ? "Nothing matches these filters." : libraries?.length ? "Scan a library to find its files." : "Add a library first."}
          </Empty>
        )}
      </Section>

      {data && data.items.length > 0 && (
        <div className="pager">
          <span className="faint small">{data.total > PAGE ? `${offset + 1}–${Math.min(offset + PAGE, data.total)} of ${data.total} files` : `${data.total} files`}</span>
          <span className="spacer" />
          {data.total > PAGE && (
            <>
              <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                Previous
              </button>
              <button className="btn sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
                Next
              </button>
            </>
          )}
        </div>
      )}

      {selected != null && <FileDrawer id={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function FileRow({ f, selected, onClick }: { f: MediaFile; selected: boolean; onClick: () => void }) {
  const st = STATUS_LABEL[f.status];
  const m = f.media;
  return (
    <tr
      className={`clickable ${selected ? "selected" : ""}`}
      onClick={onClick}
      tabIndex={0}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
          e.preventDefault();
          onClick();
        }
      }}
    >
      <td className="cell-file">
        <div className="ellipsis primary-cell" title={f.path}>
          {f.filename}
        </div>
        {f.relative_path !== f.filename && <div className="faint caption ellipsis">{f.relative_path.slice(0, -f.filename.length - 1)}</div>}
      </td>
      <td>
        {m ? (
          <span className="row gap-2 nowrap">
            <CodecChip codec={m.video_codec} />
            <span className="mono small muted">{m.resolution_label}</span>
            {m.hdr_format && <span className="chip warn">{m.hdr_format}</span>}
          </span>
        ) : (
          <span className="faint">—</span>
        )}
      </td>
      <td className="mono small nowrap">{m ? duration(m.duration) : "—"}</td>
      <td className="right mono small nowrap">
        {f.original_size && f.original_size !== f.size ? (
          <span title={`Originally ${bytes(f.original_size)}`}>
            <span className="faint">{bytes(f.original_size)} → </span>
            {bytes(f.size)}
          </span>
        ) : (
          bytes(f.size)
        )}
      </td>
      <td className="small muted nowrap">{ageDays(f.mtime)} d</td>
      <td>
        <span className={`chip ${f.ignored ? "" : st.cls}`}>{f.ignored ? "Ignored" : st.text}</span>
      </td>
      <td className="small muted cell-plan">
        <div className="ellipsis" title={f.decision ?? ""}>
          {f.decision ?? "—"}
        </div>
      </td>
    </tr>
  );
}

function FileDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const { data: f } = useFile(id);
  const { data: profiles } = useProfiles();
  const toast = useToast();
  const [profileId, setProfileId] = useState<number | null>(null);
  const [priority, setPriority] = useState(3);
  const queue = useAction(() => api.post(`/files/${id}/queue`, { profile_id: profileId ?? profiles?.[0]?.id, priority }), [["files"], ["file"], ["jobs"], ["stats"]]);
  const ignore = useAction((v: boolean) => api.post(`/files/${id}/ignore`, { ignored: v }), [["files"], ["file"]]);
  const reprobe = useAction(() => api.post(`/files/${id}/reprobe`), [["files"], ["file"]]);

  if (!f)
    return (
      <Drawer title="Loading…" onClose={onClose}>
        <Spinner />
      </Drawer>
    );
  const m = f.media;
  const ev = f.evaluation;
  const chosen = profileId ?? ev.profile_id ?? profiles?.[0]?.id ?? null;

  return (
    <Drawer
      title={f.filename}
      subtitle={<span className="mono">{f.path}</span>}
      onClose={onClose}
      actions={
        <button className="btn sm" title="Read the file's streams and metadata again" onClick={() => reprobe.mutate(undefined, { onSuccess: () => toast("Re-analyzed"), onError: (e) => toast((e as Error).message, "err") })}>
          <RefreshCw size={14} /> Re-analyze
        </button>
      }
    >
      {f.status === "error" && <Callout kind="err" title="FrameForge couldn't read this file">{f.probe_error}. Interrupted OBS recordings often look like this.</Callout>}

      <section>
        <div className="group-title">What FrameForge will do</div>
        <Callout kind={ev.action === "transcode" ? "warn" : ev.action === "skip" ? "ok" : "info"} title={ev.action === "transcode" ? "Convert" : ev.action === "skip" ? "Leave as is" : "Nothing"}>
          {ev.reason}
        </Callout>
        {ev.rules.length > 0 && (
          <details className="disclosure mt-2">
            <summary>Why? Show how each rule was checked</summary>
            <div className="stack gap-3 mt-2">
              {ev.rules.map((r) => (
                <div key={r.rule_id}>
                  <div className="row small strong gap-2">
                    {r.matched ? <Check size={14} className="text-ok" /> : <Minus size={14} className="faint" />}
                    {r.rule_name}
                  </div>
                  <ul className="trace trace-nested">
                    {r.trace.map((t, i) => (
                      <li key={i} style={{ paddingLeft: t.depth * 14 }}>
                        {t.passed ? <Check size={13} className="ok" /> : <X size={13} className="no" />}
                        <span>{t.label}</span>
                        {t.actual != null && <span className="faint">(is {t.actual})</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </details>
        )}
      </section>

      {!["missing", "error", "new"].includes(f.status) && (
        <section className="convert-box">
          <div className="group-title">Convert now</div>
          <div className="form-grid">
            <Field label="Profile">
              <select className="select" value={chosen ?? ""} onChange={(e) => setProfileId(Number(e.target.value))}>
                {profiles?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Priority">
              <select className="select" value={priority} onChange={(e) => setPriority(Number(e.target.value))}>
                {PRIORITIES.map((p, i) => (
                  <option key={p} value={i}>
                    {p}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="row wrap mt-4">
            <Toggle checked={f.ignored} onChange={(v) => ignore.mutate(v)} label="Never process this file automatically" />
            <span className="spacer" />
            <button
              className="btn primary"
              disabled={queue.isPending || f.status === "queued" || f.status === "processing"}
              onClick={() => queue.mutate(undefined, { onSuccess: () => toast("Added to the queue"), onError: (e) => toast((e as Error).message, "err") })}
            >
              Add to queue
            </button>
          </div>
        </section>
      )}

      {m && (
        <section>
          <div className="group-title">Media</div>
          <dl className="kv">
            <dt>Video</dt>
            <dd className="row wrap gap-2">
              <CodecChip codec={m.video_codec} /> {m.width}×{m.height} · {m.fps ? `${m.fps.toFixed(2)} fps` : "?"} · {m.bit_depth}-bit {m.hdr_format ?? "SDR"}
            </dd>
            <dt>Container</dt>
            <dd>{m.container.toUpperCase()}</dd>
            <dt>Duration</dt>
            <dd>{duration(m.duration)}</dd>
            <dt>Bitrate</dt>
            <dd>{m.bitrate ? `${(m.bitrate / 1e6).toFixed(1)} Mb/s` : "—"}</dd>
            <dt>Audio</dt>
            <dd>{m.audio_count ? `${m.audio_count} track(s): ${m.audio_codecs.join(", ")}` : "none"}</dd>
            <dt>Subtitles / chapters</dt>
            <dd>
              {m.subtitle_count} / {m.chapter_count}
            </dd>
            <dt>Recorded</dt>
            <dd>{m.creation_time ? date(m.creation_time) : "not stored in file"}</dd>
            <dt>Modified</dt>
            <dd>
              {date(f.mtime)} ({ageDays(f.mtime)} days ago)
            </dd>
            <dt>Size</dt>
            <dd>
              {bytes(f.size)}
              {f.original_size && f.original_size !== f.size && <span className="faint"> (originally {bytes(f.original_size)})</span>}
            </dd>
          </dl>
        </section>
      )}

      {f.jobs.length > 0 && (
        <section>
          <div className="group-title">Jobs</div>
          <table className="table mini">
            <tbody>
              {f.jobs.map((j) => (
                <tr key={j.id}>
                  <td>
                    <StateChip state={j.state} />
                  </td>
                  <td>
                    <Link className="link" to={`/jobs/${j.id}`}>
                      {j.profile_name}
                    </Link>
                  </td>
                  <td className="faint small right">{date(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </Drawer>
  );
}
