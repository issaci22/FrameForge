import type { StartingPoint } from "../../api/types";
import { CodecChip } from "../media";

/** Built-in goals as starting points: pick one, then adjust anything. */
export function StartingPoints({ points, value, onPick }: { points: StartingPoint[]; value: string | null; onPick: (p: StartingPoint) => void }) {
  const goal = points.filter((p) => p.group === "goal");
  const special = points.filter((p) => p.group === "special");
  const card = (p: StartingPoint) => (
    <button key={p.key} type="button" role="radio" aria-checked={value === p.key} className={`option-card tx-start ${value === p.key ? "on" : ""}`} onClick={() => onPick(p)}>
      <span className="t">{p.name}</span>
      <span className="row gap-1">
        <CodecChip codec={p.spec.video_codec} />
        <span className="mono small faint">{p.spec.container.toUpperCase()}</span>
      </span>
      <span className="d">{p.tagline}</span>
    </button>
  );
  return (
    <div className="stack gap-3">
      <div className="option-cards tx-cards five" role="radiogroup" aria-label="Starting point">
        {goal.map(card)}
      </div>
      {special.length > 0 && (
        <details className="disclosure">
          <summary>Special purpose</summary>
          <div className="option-cards tx-cards four mt-2" role="radiogroup" aria-label="Special-purpose starting point">
            {special.map(card)}
          </div>
        </details>
      )}
    </div>
  );
}
