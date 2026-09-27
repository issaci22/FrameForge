import type { ReactNode } from "react";
import type { Profile, ProfileOptions, ProfilePreview, ProfileSpec } from "../../api/types";
import { Callout, Segmented } from "../ui";
import type { OnSpec } from "./parts";

const SPEED_OPTIONS: { value: ProfileSpec["speed"]; label: string }[] = [
  { value: "fast", label: "Fast" },
  { value: "balanced", label: "Balanced" },
  { value: "quality", label: "Slow" },
  { value: "max", label: "Slowest" },
];

// The meter is deliberately qualitative: file size depends on the footage far more than on the number.
const SIZE_STEPS = 5;

/**
 * The Smaller file ←→ Better quality slider, with what the position means: a plain-language band, the
 * native encoder value on the user's own nodes, and (when there is data) what this profile really did.
 */
export function QualityControl({
  spec,
  onChange,
  options,
  preview,
  history,
  extra,
}: {
  spec: ProfileSpec;
  onChange: OnSpec;
  options?: ProfileOptions;
  preview?: ProfilePreview;
  history?: Profile["history"];
  extra?: ReactNode;
}) {
  const slider = spec.rate_control === "quality";
  const tiers = options?.quality_tiers ?? [];
  const tierIndex = Math.max(0, tiers.reduce((acc, t, i) => (spec.quality >= t.min ? i : acc), 0));
  const tier = preview?.tier ?? (tiers[tierIndex] ? { label: tiers[tierIndex].label, description: tiers[tierIndex].description } : null);
  const filled = tiers.length ? tierIndex + 1 : Math.ceil((spec.quality / 100) * SIZE_STEPS);
  const nodes = (preview?.nodes ?? []).filter((n) => n.native);
  const cpu = preview?.quality.find((q) => q.backend === "cpu");

  return (
    <div className="stack gap-3">
      {slider ? (
        <div className="tx-quality">
          <div className="slider-wrap">
            <div className="slider-ends">
              <span>Smaller file</span>
              <span className="tx-tier">
                <b>{tier?.label ?? "…"}</b>
                <span className="mono">{spec.quality}</span>
              </span>
              <span>Better quality</span>
            </div>
            <input
              type="range"
              className="slider"
              min={0}
              max={100}
              value={spec.quality}
              aria-label="Quality, 0 to 100"
              aria-valuetext={`${spec.quality}, ${tier?.label ?? ""}${cpu ? `, ${cpu.param} ${cpu.value} on the CPU encoder` : ""}`}
              style={{ ["--fill" as string]: `${spec.quality}%` }}
              onChange={(e) => onChange({ quality: Number(e.target.value) })}
            />
          </div>
          <div className="tx-meaning">
            <div className="tx-size" title="Relative file size for the same source. The real size depends mostly on the footage.">
              <span className="faint small">File size</span>
              <span className="tx-bars" aria-hidden>
                {Array.from({ length: SIZE_STEPS }, (_, i) => (
                  <span key={i} className={i < filled ? "on" : ""} />
                ))}
              </span>
            </div>
            {tier && <div className="small muted">{tier.description}</div>}
          </div>
        </div>
      ) : (
        <Callout kind="info" title={spec.rate_control === "bitrate" ? `Fixed bitrate: ${spec.bitrate_kbps ?? "?"} kb/s` : `Exact encoder value: ${spec.constant_quality ?? "?"}`}>
          Set under Advanced → Rate control, so the slider isn't used.{" "}
          <button type="button" className="btn sm ghost" onClick={() => onChange({ rate_control: "quality" })}>
            Use the slider again
          </button>
        </Callout>
      )}

      {slider && (nodes.length > 0 || cpu) && (
        <div className="tx-native small">
          <span className="faint">Means</span>
          {nodes.length > 0
            ? nodes.map((n) => (
                <span key={n.node_id} className="chip outline mono" title={`${n.name} would use ${n.encoder}`}>
                  {n.native} <span className="faint">· {n.name}</span>
                </span>
              ))
            : cpu && (
                <span className="chip outline mono" title="No node is online; this is the software encoder's value">
                  {cpu.param} {cpu.value} <span className="faint">· {cpu.encoder}</span>
                </span>
              )}
          {preview && preview.quality.length > 1 && (
            <details className="disclosure tx-inline">
              <summary>Every encoder</summary>
              <div className="row wrap gap-2 mt-2">
                {preview.quality.map((q) => (
                  <span key={q.backend} className="chip outline mono" title={`${q.label}: range ${q.range[0]}–${q.range[1]}, lower = better`}>
                    {q.encoder} {q.param} {q.value}
                  </span>
                ))}
              </div>
              <div className="faint small mt-2">
                Each encoder has its own scale (CRF, CQ, ICQ, QP), so equal numbers don't mean equal quality. FrameForge calibrates each one to give similar results at the same slider position.
              </div>
            </details>
          )}
        </div>
      )}

      {history && history.ratio != null && (
        <div className="small">
          <span className="faint">Your last {history.jobs} conversions with this profile:</span> outputs averaged <b className="mono">{Math.round(history.ratio * 100)}%</b> of the original size.
        </div>
      )}

      <div className="tx-row">
        <span className="tx-label">Encoding effort</span>
        <Segmented value={spec.speed} onChange={(v) => onChange({ speed: v })} options={SPEED_OPTIONS} />
        <span className="faint small">Slower squeezes out a few percent more at the same quality.</span>
      </div>
      {extra}
    </div>
  );
}
