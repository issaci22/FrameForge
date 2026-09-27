import type { CompatIssue, ProfileOptions, ProfileSpec } from "../../api/types";
import { Segmented } from "../ui";
import { CompatIssues, HwBadge, hardwareFor, type OnSpec } from "./parts";

const RES_OPTIONS: { value: number | null; label: string }[] = [
  { value: null, label: "Keep" },
  { value: 2160, label: "4K" },
  { value: 1440, label: "1440p" },
  { value: 1080, label: "1080p" },
  { value: 720, label: "720p" },
];
const FPS_OPTIONS: { value: number | null; label: string }[] = [
  { value: null, label: "Keep" },
  { value: 60, label: "60" },
  { value: 30, label: "30" },
];

/** Container and video codec as two separate choices, with what each implies and whether a node can encode it. */
export function FormatSection({ spec, onChange, options, issues }: { spec: ProfileSpec; onChange: OnSpec; options?: ProfileOptions; issues?: CompatIssue[] }) {
  const containers = options?.containers ?? [];
  const codecs = options?.video_codecs ?? [];
  const remux = spec.video_codec === "copy";
  return (
    <div className="stack gap-3">
      <div>
        <div className="tx-label">Container</div>
        <div className="option-cards tx-cards two" role="radiogroup" aria-label="Container">
          {containers.map((c) => (
            <button key={c.value} type="button" role="radio" aria-checked={spec.container === c.value} className={`option-card ${spec.container === c.value ? "on" : ""}`} onClick={() => onChange({ container: c.value })}>
              <span className="t mono">{c.label}</span>
              <span className="d">{c.description}</span>
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="tx-label">Video codec</div>
        <div className="option-cards tx-cards four" role="radiogroup" aria-label="Video codec">
          {codecs.map((c) => {
            const hw = hardwareFor(options, c.value);
            return (
              <button key={c.value} type="button" role="radio" aria-checked={spec.video_codec === c.value} className={`option-card ${spec.video_codec === c.value ? "on" : ""}`} onClick={() => onChange({ video_codec: c.value })}>
                <span className="t">
                  <span className={`codec ${c.value === "copy" ? "other" : c.value}`}>{c.label}</span>
                  {c.value !== "copy" && <HwBadge state={hw.state} title={hw.detail} />}
                </span>
                <span className="d">{c.description}</span>
                <span className="d mono faint">{c.value === "copy" ? "size unchanged" : `size ${c.efficiency}`}</span>
              </button>
            );
          })}
        </div>
      </div>
      <CompatIssues issues={issues} fields={["container", "video_codec", "hw_mode"]} onApply={onChange} />
      {!remux && (
        <div className="tx-grid">
          <div className="tx-row">
            <span className="tx-label">Max resolution</span>
            <Segmented value={spec.max_resolution} onChange={(v) => onChange({ max_resolution: v })} options={RES_OPTIONS} />
          </div>
          <div className="tx-row">
            <span className="tx-label">Max frame rate</span>
            <Segmented value={spec.max_fps} onChange={(v) => onChange({ max_fps: v })} options={FPS_OPTIONS} />
          </div>
          <div className="faint small tx-span">Limits never upscale. Resolution applies to the short side, so vertical video stays sharp.</div>
        </div>
      )}
      <CompatIssues issues={issues} fields={["max_resolution"]} onApply={onChange} />
    </div>
  );
}
