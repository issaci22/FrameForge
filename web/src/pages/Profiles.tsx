import { useState } from "react";
import { Copy, Pencil, Plus, X } from "lucide-react";
import { api } from "../api/client";
import { useAction, useProfileOptions, useProfiles } from "../api/hooks";
import type { Profile } from "../api/types";
import { CodecChip } from "../components/media";
import { Callout, ErrorState, LoadingState, PageHeader, Section } from "../components/ui";
import { ProfileEditor } from "./profiles/ProfileEditor";

const GROUPS: { key: string; title: string; hint: string; match: (p: Profile) => boolean }[] = [
  { key: "goal", title: "Starting points", hint: "Built-in goals. Edit them, or duplicate one to make your own.", match: (p) => p.builtin_group === "goal" },
  { key: "mine", title: "Your profiles", hint: "", match: (p) => !p.builtin },
  { key: "special", title: "Special purpose", hint: "Built-ins for uploads, proxies and remuxing.", match: (p) => p.builtin_group === "special" },
  { key: "legacy", title: "From an earlier version", hint: "Built-ins you had changed before the lineup was renamed. Kept exactly as you left them.", match: (p) => p.builtin_group === "legacy" },
];

export function ProfilesPage() {
  const { data: profiles, error, refetch } = useProfiles();
  const { data: options } = useProfileOptions();
  const [editing, setEditing] = useState<Profile | "new" | null>(null);
  const [copyOf, setCopyOf] = useState<Profile | null>(null);
  const dismiss = useAction(() => api.post("/profiles/notices/dismiss"), [["profiles", "options"]]);

  return (
    <div className="page">
      <PageHeader
        title="Profiles"
        description="What a converted file should look like. Rules decide which profile a file gets; the library decides where it goes."
        actions={
          <button
            className="btn primary"
            onClick={() => {
              setCopyOf(null);
              setEditing("new");
            }}
          >
            <Plus size={16} /> New profile
          </button>
        }
      />

      {options?.notices && options.notices.length > 0 && (
        <div className="mb-section">
          <Callout kind="info" title="Built-in profiles were renamed by goal">
            <div className="stack gap-1">
              {options.notices.map((n) => (
                <div key={n}>{n}</div>
              ))}
              <div>
                <button className="btn sm ghost" onClick={() => dismiss.mutate(undefined)}>
                  <X size={14} /> Got it
                </button>
              </div>
            </div>
          </Callout>
        </div>
      )}

      {error && !profiles ? (
        <ErrorState title="Couldn't load profiles" error={error} onRetry={() => refetch()} />
      ) : !profiles ? (
        <LoadingState />
      ) : (
        GROUPS.map((g) => {
          const rows = profiles.filter(g.match);
          if (!rows.length) return null;
          return (
            <Section key={g.key} title={g.title} description={g.hint || undefined} flush>
              <div className="table-scroll">
                <table className="table profiles-table">
                  <thead>
                    <tr>
                      <th>Profile</th>
                      <th>Output</th>
                      <th>Settings</th>
                      <th>Real results</th>
                      <th>Used by</th>
                      <th className="col-actions">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((p) => (
                      <tr
                        key={p.id}
                        className="clickable"
                        tabIndex={0}
                        onClick={() => setEditing(p)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && e.target === e.currentTarget) setEditing(p);
                        }}
                      >
                        <td className="cell-profile">
                          <div className="primary-cell">{p.name}</div>
                          <div className="faint small">{p.description}</div>
                        </td>
                        <td>
                          <span className="row gap-2 nowrap">
                            <CodecChip codec={p.spec.video_codec} />
                            <span className="mono small muted">{p.spec.container.toUpperCase()}</span>
                          </span>
                        </td>
                        <td className="small muted">{p.summary}</td>
                        <td className="small nowrap">
                          {p.history?.ratio != null ? (
                            <span title={`Average over ${p.history.jobs} completed jobs since this profile last changed`}>
                              <span className="mono">{Math.round(p.history.ratio * 100)}%</span> <span className="faint">of original</span>
                            </span>
                          ) : (
                            <span className="faint">—</span>
                          )}
                        </td>
                        <td className="small nowrap">{p.rule_count ? `${p.rule_count} rule(s)` : <span className="faint">—</span>}</td>
                        <td className="col-actions">
                          <div className="row end gap-1">
                            <button
                              className="btn sm ghost icon"
                              title="Duplicate"
                              aria-label={`Duplicate ${p.name}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setCopyOf(p);
                                setEditing("new");
                              }}
                            >
                              <Copy size={14} />
                            </button>
                            <button
                              className="btn sm ghost icon"
                              title="Edit"
                              aria-label={`Edit ${p.name}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditing(p);
                              }}
                            >
                              <Pencil size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          );
        })
      )}

      {editing && <ProfileEditor profile={editing === "new" ? null : editing} base={copyOf} onClose={() => setEditing(null)} />}
    </div>
  );
}
