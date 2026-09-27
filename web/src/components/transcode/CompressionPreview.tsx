import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Eye, Film, Search, SplitSquareHorizontal } from "lucide-react";
import { api } from "../../api/client";
import { useDebounced, useFiles } from "../../api/hooks";
import type { CompressionPreview, MediaFile, ProfilePreview, ProfileSpec } from "../../api/types";
import { putPreview, useLive } from "../../live/events";
import { bytes } from "../../lib/format";
import { Timeline } from "../media";
import { Callout, Modal, Segmented } from "../ui";

/** "Preview on a file…": optional, so it never slows down people who just want to save a profile. */
export function CompressionPreviewButton({ spec, availability }: { spec: ProfileSpec; availability?: ProfilePreview["compression_preview"] }) {
  const [open, setOpen] = useState(false);
  const disabled = !availability?.available;
  return (
    <>
      <div className="tx-row">
        <button type="button" className="btn sm" disabled={disabled} title={availability?.reason ?? undefined} onClick={() => setOpen(true)}>
          <Eye size={12} /> Preview on a file…
        </button>
        <span className="faint small">{disabled ? availability?.reason ?? "Checking nodes…" : `Encodes three short samples on ${availability?.node} and compares frames with the original.`}</span>
      </div>
      {open && <PreviewDialog spec={spec} onClose={() => setOpen(false)} />}
    </>
  );
}

function PreviewDialog({ spec, onClose }: { spec: ProfileSpec; onClose: () => void }) {
  const [file, setFile] = useState<MediaFile | null>(null);
  const [id, setId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const preview = useLive((s) => (id ? s.previews[id] : undefined));
  const running = preview?.state === "running";

  // Closing the dialog stops a running preview so the node's slot is freed for jobs.
  const idRef = useRef<string | null>(null);
  idRef.current = running ? id : null;
  useEffect(() => () => void (idRef.current && api.del(`/previews/${idRef.current}`).catch(() => undefined)), []);

  const start = async () => {
    if (!file) return;
    setStarting(true);
    setError(null);
    try {
      const p = await api.post<CompressionPreview>("/previews", { file_id: file.id, spec });
      putPreview(p);
      setId(p.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  return (
    <Modal
      wide
      title="Compression preview"
      subtitle="Short samples encoded exactly like a real job, on a real node. Nothing is written to your library."
      onClose={onClose}
      footer={
        <>
          {running && (
            <button type="button" className="btn" onClick={() => id && api.del(`/previews/${id}`).catch(() => undefined)}>
              Stop
            </button>
          )}
          <span className="spacer" />
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
          <button type="button" className="btn primary" disabled={!file || starting || running} onClick={start}>
            <Film size={12} /> {preview && !running ? "Run again" : "Make preview"}
          </button>
        </>
      }
    >
      <div className="stack">
        <FilePicker value={file} onChange={(f) => setFile(f)} />
        {error && <Callout kind="err" title="Couldn't start the preview">{error}</Callout>}
        {preview && <PreviewResult preview={preview} />}
      </div>
    </Modal>
  );
}

function FilePicker({ value, onChange }: { value: MediaFile | null; onChange: (f: MediaFile) => void }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q, 250);
  const { data } = useFiles({ q: query || undefined, limit: 8, sort: "size", order: "desc" });
  const items = (data?.items ?? []).filter((f) => f.media && f.status !== "missing" && f.status !== "error");
  return (
    <div className="stack gap-2">
      <label className="tx-search">
        <Search size={13} aria-hidden />
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a representative recording (name or folder)…" aria-label="Search files" />
      </label>
      <div className="tx-files" role="listbox" aria-label="Files">
        {items.map((f) => (
          <button key={f.id} type="button" role="option" aria-selected={value?.id === f.id} className={`tx-file ${value?.id === f.id ? "on" : ""}`} onClick={() => onChange(f)}>
            <span className="ellipsis">{f.relative_path}</span>
            <span className="mono small faint">
              {f.media?.video_codec_label} · {f.media?.resolution_label} · {bytes(f.size)}
            </span>
          </button>
        ))}
        {data && items.length === 0 && <div className="faint small">No analyzed files match.</div>}
      </div>
    </div>
  );
}

function PreviewResult({ preview }: { preview: CompressionPreview }) {
  const [index, setIndex] = useState(0);
  if (preview.state === "running") {
    return (
      <div className="tx-progress">
        <div className="small">
          {preview.message ?? "Starting…"} <span className="faint">on {preview.node_name}</span>
        </div>
        <Timeline value={preview.total ? (preview.done / preview.total) * 100 : 0} state={preview.done ? "run" : "wait"} thin />
      </div>
    );
  }
  if (preview.state !== "done") {
    return <Callout kind={preview.state === "cancelled" ? "info" : "err"} title={preview.state === "cancelled" ? "Preview stopped" : "The preview failed"}>{preview.error}</Callout>;
  }
  const sample = preview.samples[Math.min(index, preview.samples.length - 1)];
  const est = preview.estimate;
  return (
    <div className="stack gap-3">
      <div className="row wrap small gap-3">
        <span className="chip outline mono">{preview.encoder}</span>
        {preview.quality_label && <span className="chip outline mono">{preview.quality_label}</span>}
        <span className="faint">on {preview.node_name}</span>
        {est && (
          <span title={est.basis}>
            <span className="faint">Rough video size:</span> <b className="mono">{bytes(est.video_bytes_low)}–{bytes(est.video_bytes_high)}</b>
            {est.ratio != null && <span className="faint"> (≈{Math.round(est.ratio * 100)}% of the file, video only)</span>}
          </span>
        )}
      </div>
      {sample && <Comparison key={sample.index} original={sample.original_url} encoded={sample.encoded_url} width={sample.width} height={sample.height} />}
      <div className="tx-thumbs">
        {preview.samples.map((s, i) => (
          <button key={s.index} type="button" className={`tx-thumb ${i === index ? "on" : ""}`} onClick={() => setIndex(i)}>
            <img src={s.encoded_url} alt={`Sample at ${fmtTime(s.position)}`} />
            <span className="mono small">{fmtTime(s.position)}</span>
          </button>
        ))}
      </div>
      <div className="faint small">
        Look for blocking in dark areas and gradients (sky, walls), smeared textures (grass, hair, film grain), and halos around text and edges. {preview.notes.join(" ")}
      </div>
    </div>
  );
}

const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

type Mode = "split" | "toggle";

/** Original vs encoded: a draggable split, or a press-and-hold A/B toggle, both zoomable to inspect artifacts. */
function Comparison({ original, encoded, width, height }: { original: string; encoded: string; width: number; height: number }) {
  const [mode, setMode] = useState<Mode>("split");
  const [zoom, setZoom] = useState(1);
  const [split, setSplit] = useState(50);
  const [showOriginal, setShowOriginal] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ kind: "split" | "pan"; x: number; y: number; pan: { x: number; y: number } } | null>(null);

  useEffect(() => setPan({ x: 0, y: 0 }), [zoom]);

  const onDown = (e: ReactPointerEvent<HTMLDivElement>, kind: "split" | "pan") => {
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { kind, x: e.clientX, y: e.clientY, pan };
    if (kind === "split") moveSplit(e.clientX);
  };
  const moveSplit = (clientX: number) => {
    const r = frame.current?.getBoundingClientRect();
    if (r) setSplit(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)));
  };
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === "split") moveSplit(e.clientX);
    else setPan({ x: d.pan.x + (e.clientX - d.x) / zoom, y: d.pan.y + (e.clientY - d.y) / zoom });
  };
  const onUp = () => (drag.current = null);

  const layer = { transform: `scale(${zoom}) translate(${pan.x}px, ${pan.y}px)` };
  const showOrig = mode === "toggle" && showOriginal;
  return (
    <div className="stack gap-2">
      <div className="row wrap gap-3">
        <Segmented
          value={mode}
          onChange={setMode}
          options={[
            { value: "split", label: <span className="row gap-1"><SplitSquareHorizontal size={12} /> Split</span> },
            { value: "toggle", label: "A / B" },
          ]}
        />
        <Segmented value={zoom} onChange={setZoom} options={[{ value: 1, label: "Fit" }, { value: 2, label: "2×" }, { value: 4, label: "4×" }]} />
        <span className="faint small">{zoom > 1 ? "Drag the picture to look around." : "Zoom in to inspect fine detail."}</span>
        <span className="right mono small faint">
          {width}×{height}
        </span>
      </div>
      <div
        ref={frame}
        className={`tx-compare ${zoom > 1 ? "zoomed" : ""}`}
        style={{ aspectRatio: `${width} / ${height}` }}
        onPointerDown={(e) => (zoom > 1 ? onDown(e, "pan") : mode === "split" && onDown(e, "split"))}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
      >
        <img className="tx-layer" src={showOrig ? original : encoded} alt={showOrig ? "Original frame" : "Encoded frame"} style={layer} draggable={false} />
        {mode === "split" && (
          <>
            <img className="tx-layer" src={original} alt="Original frame" style={{ ...layer, clipPath: `inset(0 ${100 - split}% 0 0)` }} draggable={false} />
            <div className="tx-divider" style={{ left: `${split}%` }} onPointerDown={(e) => onDown(e, "split")}>
              <span />
            </div>
            <span className="tx-tag left">Original</span>
            <span className="tx-tag right">Encoded</span>
          </>
        )}
        {mode === "toggle" && <span className="tx-tag left">{showOrig ? "Original" : "Encoded"}</span>}
      </div>
      {mode === "toggle" && (
        <button type="button" className="btn sm" onPointerDown={() => setShowOriginal(true)} onPointerUp={() => setShowOriginal(false)} onPointerLeave={() => setShowOriginal(false)} onKeyDown={(e) => e.key === " " && setShowOriginal(true)} onKeyUp={() => setShowOriginal(false)}>
          Hold to show the original
        </button>
      )}
    </div>
  );
}
