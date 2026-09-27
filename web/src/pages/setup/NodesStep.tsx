import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { keys, useAction } from "../../api/hooks";
import type { Node } from "../../api/types";
import { Callout, CopyBlock, Field, useToast } from "../../components/ui";
import { ago } from "../../lib/format";
import { isLoopbackUrl } from "../../lib/url";
import { EncoderMatrix } from "../NodeDetail";
import { AddNodeWizard, PathMappingsEditor } from "../Nodes";
import { effectivePublicUrl, nodeDraft, type Issue, type NodeDraft } from "./model";
import { GuideSection, issueProps, IssueList, Kv, Panel, StatusChip, StepFrame, type StepProps } from "./parts";

const DETECT = [
  "# Server with the built-in node (FF_ROLE=all)",
  "docker compose exec frameforge python -m frameforge_node --detect",
  "",
  "# A remote node started from docker/compose/node.yml",
  "docker compose -f docker/compose/node.yml exec frameforge-node python -m frameforge_node --detect",
].join("\n");

const STATUS_TONE = { online: "ok", pending: "warn", paused: "info", offline: "neutral", disabled: "neutral" } as const;

export function NodesStep({ draft, update, server, issues, showErrors }: StepProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const nodes = server.nodes;
  const url = effectivePublicUrl(draft, server.settings);
  const snippetUrl = url.trim() || window.location.origin;
  const urlChanged = !!server.settings && (url.trim() || null) !== (server.settings.public_url ?? null);
  const urlIssue = issues.find((i) => i.field === "public_url");

  const addNode = async () => {
    // The enrollment snippet is generated from the saved setting, so save a changed URL first.
    if (urlChanged && server.settings) {
      setSaving(true);
      try {
        await api.put("/settings", { ...server.settings, public_url: url.trim() || null });
        await qc.invalidateQueries({ queryKey: keys.settings });
      } catch (e) {
        toast((e as Error).message, "err");
        return;
      } finally {
        setSaving(false);
      }
    }
    setAdding(true);
  };

  return (
    <StepFrame
      step="nodes"
      title="Check your transcoding hardware"
      lede="Nodes are the machines that transcode. Each one tests its encoders with a real short encode on its own hardware, and only encoders that pass are ever used or shown as available. Nothing here needs changing unless you run remote nodes."
      guide={<NodesGuide />}
    >
      {!nodes ? (
        <div className="faint small">Loading nodes…</div>
      ) : nodes.length === 0 ? (
        <Callout kind="warn" title="No nodes yet">
          This server runs without a built-in node (FF_ROLE=server), so jobs need a remote node. Add one below, now or after setup.
        </Callout>
      ) : (
        nodes.map((n) => (
          <NodeCard
            key={n.id}
            node={n}
            draft={nodeDraft(draft, n)}
            onChange={(patch) => update((d) => ({ ...d, nodes: { ...d.nodes, [n.id]: { ...nodeDraft(d, n), ...patch } } }))}
            issues={issues.filter((i) => i.key === String(n.id))}
            showErrors={showErrors}
          />
        ))
      )}

      <Panel
        title="Capability detection"
        description={
          <>
            Run the same tests by hand for the full report: the GPUs a machine sees, the VA-API/QSV device it uses, and <span className="mono">OK</span> or <span className="mono">FAIL</span> with a reason for every encoder and hardware decoder. It doesn't connect to the server.
          </>
        }
      >
        <CopyBlock text={DETECT} />
        <div className="faint small">After a driver update or a hardware change, use “Re-detect hardware” on the node so the server sees the new result.</div>
      </Panel>

      <Panel
        title="Remote nodes"
        description="Optional. Any machine running the FrameForge image in node mode can take jobs. It connects out to this server (no open ports on the node), must run the same FrameForge version, and must mount the same media: there is no file transfer."
      >
        <Field label="Server URL for nodes" htmlFor="setup-public-url" help="The address other machines use to reach this server. Empty uses the address you're browsing from." {...issueProps(issues, "public_url", showErrors || !!urlIssue)}>
          <input id="setup-public-url" className="input mono" value={url} placeholder="http://192.168.1.20:8686" onChange={(e) => update((d) => ({ ...d, publicUrl: e.target.value }))} />
        </Field>
        {isLoopbackUrl(snippetUrl) && (
          <Callout kind="warn" title="Other machines can't reach this address">
            Node snippets would point at <span className="mono">{snippetUrl}</span>, which on another machine means that machine itself. Enter this server's LAN address above.
          </Callout>
        )}
        <div className="row wrap">
          <button type="button" className="btn" disabled={saving || !!urlIssue} onClick={addNode}>
            <Plus size={14} /> {saving ? "Saving server URL…" : "Add remote node…"}
          </button>
          <span className="faint small">Creates the node right away and shows its token once, with a docker-compose snippet{urlChanged ? ". The server URL is saved first" : ""}.</span>
        </div>
      </Panel>

      {adding && (
        <AddNodeWizard
          onClose={() => {
            setAdding(false);
            qc.invalidateQueries({ queryKey: keys.nodes });
          }}
        />
      )}
    </StepFrame>
  );
}

function NodeCard({ node, draft, onChange, issues, showErrors }: { node: Node; draft: NodeDraft; onChange: (p: Partial<NodeDraft>) => void; issues: Issue[]; showErrors: boolean }) {
  const toast = useToast();
  const redetect = useAction(() => api.post(`/nodes/${node.id}/redetect`), []);
  const caps = node.capabilities;
  const gpus = caps?.gpus.map((g) => `${g.name}${g.vram_total_mb ? ` · ${(g.vram_total_mb / 1024).toFixed(0)} GB` : ""}`) ?? [];
  const decoders = caps?.decoders.filter((d) => d.verified).map((d) => `${d.backend.toUpperCase()} ${d.codec}`) ?? [];

  return (
    <section className="panel">
      <div className="panel-head">
        <span className={`status-dot ${node.status}`} />
        <h3>{node.name}</h3>
        <StatusChip tone={STATUS_TONE[node.status]}>{node.status}</StatusChip>
        {node.is_local && <span className="chip outline">Built into the server</span>}
        <div className="right">
          {node.online && (
            <button
              type="button"
              className="btn sm"
              disabled={redetect.isPending}
              onClick={() => redetect.mutate(undefined, { onSuccess: () => toast("Re-detecting hardware. Results appear here when the tests finish."), onError: (e) => toast((e as Error).message, "err") })}
            >
              <RefreshCw size={12} /> Re-detect hardware
            </button>
          )}
        </div>
      </div>
      <div className="panel-body stack">
        {caps ? (
          <div className="node-grid">
            <Kv
              rows={[
                ["Platform", caps.os],
                ["CPU", `${caps.cpu_model} · ${caps.cpu_threads} threads`],
                ["Memory", `${(caps.ram_total_mb / 1024).toFixed(1)} GB`],
                ["GPU", gpus.length ? gpus.join(", ") : <span className="faint">None visible in the container</span>],
                ["VA-API / QSV", caps.render_device ? <span className="mono">{caps.render_device}</span> : <span className="faint">No render device</span>],
                ["FFmpeg", <span className="small">{caps.ffmpeg_version ?? "not found"}</span>],
                ["Suggested jobs", <span className="mono">{caps.recommended_concurrency} at a time</span>],
                ["Tested", ago(new Date(caps.detected_at * 1000).toISOString())],
              ]}
            />
            <div className="stack gap-2">
              <div className="table-scroll">
                <EncoderMatrix caps={caps} />
              </div>
              <div className="small">
                <span className="faint">Hardware decoding: </span>
                {decoders.join(", ") || "none verified"}
              </div>
            </div>
          </div>
        ) : node.online ? (
          <div className="muted small">Detecting hardware: the node is running a short test encode for every encoder. This page updates when it finishes.</div>
        ) : node.status === "pending" ? (
          <div className="muted small">Waiting for this node to connect for the first time.</div>
        ) : (
          <div className="muted small">Offline, last seen {ago(node.last_seen_at)}. Its last hardware report appears once it reconnects.</div>
        )}
        {caps?.notes.map((note) => (
          <Callout key={note} kind="info">
            {note}
          </Callout>
        ))}
        {!node.is_local && (
          <Field label="Path mappings" help="Server path → path on this node, only when the node mounts your media somewhere else.">
            <PathMappingsEditor value={draft.path_mappings} onChange={(m) => onChange({ path_mappings: m })} />
          </Field>
        )}
        <IssueList issues={issues.filter((i) => i.field?.startsWith("map:"))} showErrors={showErrors} />
      </div>
    </section>
  );
}

function NodesGuide() {
  return (
    <>
      <GuideSection title="Reading the encoder grid">
        <Kv
          rows={[
            [<span className="cap-yes">✓ name</span>, "Passed a real test encode. The scheduler may use it."],
            [<span className="cap-no">✕ reason</span>, "Present but failed its test, with the reason."],
            [<span className="cap-no">not in build</span>, "This FFmpeg build has no such encoder."],
          ]}
        />
        <div>Expected failures: Quick Sync and VA-API always fail under Docker Desktop (WSL2 has no /dev/dri), NVENC AV1 needs an RTX 40-series or newer, and AMF isn't expected in Linux containers.</div>
      </GuideSection>
      <GuideSection title="Path mappings">
        <table className="table mini">
          <thead>
            <tr>
              <th>Server path</th>
              <th>Node path</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="mono">/media/vods</td>
              <td className="mono">/mnt/vods</td>
            </tr>
          </tbody>
        </table>
        <div>The longest matching server prefix wins, and only whole folder names match. A node that can't see a file declines the job and the scheduler tries another node.</div>
      </GuideSection>
      <GuideSection title="GPUs in Docker">
        <div>A GPU is only detected when the container can use it: pass the GPU overlay file on every docker compose up (docs/gpu.md). To use several GPUs at once, run one node container per GPU.</div>
      </GuideSection>
    </>
  );
}
