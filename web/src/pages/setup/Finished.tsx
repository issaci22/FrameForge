import { useEffect, type ReactNode } from "react";
import { ArrowRight, Eye, FolderSearch, Play, Settings2, type LucideIcon } from "lucide-react";
import { Callout } from "../../components/ui";
import type { SetupDraft } from "./model";

export interface FinishedInfo {
  libraries: string[];
  automation: SetupDraft["automation"];
  warnings: string[];
}

/** Shown once Complete setup has applied everything: what happens next, and anything worth a look. */
export function Finished({ info, onOpen }: { info: FinishedInfo; onOpen: () => void }) {
  useEffect(() => {
    document.getElementById("setup-step-title")?.focus();
  }, []);

  const n = info.libraries.length;
  const next: { icon: LucideIcon; text: ReactNode }[] = [];
  if (n > 0)
    next.push({
      icon: FolderSearch,
      text: (
        <>
          <b>
            {n === 1 ? `“${info.libraries[0]}” is` : `${n} libraries are`} being scanned now.
          </b>{" "}
          Large libraries take a while to analyze; progress shows on the Libraries page.
        </>
      ),
    });
  next.push(
    info.automation === "auto"
      ? { icon: Play, text: <><b>Matching files are queued automatically</b> when a scan finishes.</> }
      : { icon: Eye, text: <><b>Nothing is queued yet.</b> The Files page shows what each rule would do. Turn on automatic jobs per library when the decisions look right.</> },
    { icon: Settings2, text: <>Everything you set here can be changed later on the Libraries, Profiles, Rules, Nodes and Settings pages.</> },
  );

  return (
    <div className="hero finished">
      <svg className="done-mark" viewBox="0 0 64 64" aria-hidden>
        <circle className="ring" cx="32" cy="32" r="29" />
        <path className="tick" d="M20 33.5 28.5 42 45 24.5" />
      </svg>
      <h1 id="setup-step-title" tabIndex={-1} className="hero-title">
        You're all set
      </h1>
      <p className="hero-lede">FrameForge is configured{n > 0 ? " and getting to know your footage" : ""}. Here's what happens next.</p>

      <ul className="next-list">
        {next.map(({ icon: Icon, text }, i) => (
          <li key={i}>
            <span className="plan-icon" aria-hidden>
              <Icon size={16} />
            </span>
            <span>{text}</span>
          </li>
        ))}
      </ul>

      {info.warnings.length > 0 && (
        <div className="finished-warn">
          <Callout kind="warn" title={info.warnings.length === 1 ? "One thing to check" : `${info.warnings.length} things to check`}>
            <ul>
              {info.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </Callout>
        </div>
      )}

      <div className="hero-cta">
        <button type="button" className="btn primary lg" onClick={onOpen}>
          Open dashboard <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}
