import { XCircle } from "lucide-react";
import type { Diagnosis } from "../api/types";

/** Human explanation first, raw details behind a disclosure. */
export function DiagnosisPanel({ diagnosis, log }: { diagnosis: Diagnosis; log?: string | null }) {
  return (
    <div className="callout err diagnosis">
      <XCircle size={18} className="icon" color="var(--err)" aria-hidden />
      <div className="stack gap-3 flex-1">
        <div>
          <div className="title diag-title">{diagnosis.title}</div>
          <div className="text">{diagnosis.explanation}</div>
        </div>
        {diagnosis.causes.length > 0 && (
          <div>
            <div className="small strong">Possible causes</div>
            <ul>
              {diagnosis.causes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        )}
        {(diagnosis.technical || log) && (
          <details className="disclosure">
            <summary>View technical details</summary>
            <div className="stack gap-2 mt-2">
              {diagnosis.technical && <pre className="log short">{diagnosis.technical}</pre>}
              {log && <pre className="log">{log}</pre>}
            </div>
          </details>
        )}
        <div className="faint small">The source file was not modified.</div>
      </div>
    </div>
  );
}
