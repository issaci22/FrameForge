import { useState } from "react";
import { Link } from "react-router-dom";
import { Cpu, Plus, Server, X } from "lucide-react";
import { api } from "../api/client";
import { useAction, useNodes } from "../api/hooks";
import type { Node, NodeEnrollment, PathMapping } from "../api/types";
import { Meter } from "../components/media";
import { Callout, CopyBlock, Empty, ErrorState, Field, LoadingState, Modal, NumberInput, PageHeader, Section, Segmented, Tabs } from "../components/ui";
import { useLive } from "../live/events";
import { ago } from "../lib/format";
import { isLoopbackUrl } from "../lib/url";

export function NodesPage() {
  const { data: nodes, error, refetch } = useNodes();
  const [adding, setAdding] = useState(false);

  return (
    <div className="page">
      <PageHeader
        title="Nodes"
        description="Machines that do the transcoding. Add as many as you like; each one reports what its hardware can actually do."
        actions={
          <button className="btn primary" onClick={() => setAdding(true)}>
            <Plus size={16} /> Add node
          </button>
        }
      />

      {error && !nodes ? (
        <ErrorState title="Couldn't load nodes" error={error} onRetry={() => refetch()} />
      ) : !nodes ? (
        <LoadingState rows={2} />
      ) : (
        <Section flush>
          {nodes.length === 0 ? (
            <Empty
              icon={<Server size={20} />}
              title="No nodes"
              action={
                <button className="btn primary" onClick={() => setAdding(true)}>
                  <Plus size={16} /> Add node
                </button>
              }
            >
              A node is any machine running the FrameForge container in node mode.
            </Empty>
          ) : (
            <div className="table-scroll">
              <table className="table hover nodes-table">
                <thead>
                  <tr>
                    <th>Node</th>
                    <th>Hardware</th>
                    <th>Acceleration</th>
                    <th className="col-load">Load</th>
                    <th>Jobs</th>
                  </tr>
                </thead>
                <tbody>
                  {nodes.map((n) => (
                    <NodeRow key={n.id} node={n} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {adding && <AddNodeWizard onClose={() => setAdding(false)} />}
    </div>
  );
}

export function accelSummary(node: Node): string[] {
  const enc = node.capabilities?.encoders.filter((e) => e.verified && e.backend !== "cpu") ?? [];
  const byBackend = new Map<string, string[]>();
  for (const e of enc) byBackend.set(e.backend, [...(byBackend.get(e.backend) ?? []), e.codec]);
  return [...byBackend.entries()].map(([b, codecs]) => `${b.toUpperCase()} ${codecs.map((c) => (c === "hevc" ? "H.265" : c === "h264" ? "H.264" : "AV1")).join("/")}`);
}

function NodeRow({ node }: { node: Node }) {
  const live = useLive((s) => s.metrics[node.id]);
  const m = live?.metrics ?? node.metrics;
  const caps = node.capabilities;
  const accel = accelSummary(node);
  const active = live?.active ?? node.active_jobs;
  return (
    <tr>
      <td>
        <Link to={`/nodes/${node.id}`} className="node-title">
          <span className={`status-dot ${node.status}`} />
          <span className="ellipsis">{node.name}</span>
        </Link>
        <div className="faint small node-sub">
          {node.status === "online" ? (node.is_local ? "Built into the server" : `v${node.version}`) : node.status === "pending" ? "Waiting for first connection" : `${node.status} · last seen ${ago(node.last_seen_at)}`}
        </div>
      </td>
      <td className="small">
        {caps ? (
          <>
            <div className="ellipsis cell-cpu">{caps.cpu_model}</div>
            <div className="faint">
              {caps.cpu_threads} threads · {(caps.ram_total_mb / 1024).toFixed(0)} GB
              {caps.gpus.map((g) => ` · ${g.name}`).join("")}
            </div>
          </>
        ) : (
          <span className="faint">Not detected yet</span>
        )}
      </td>
      <td>
        <div className="row wrap gap-1">
          {accel.length ? accel.map((a) => <span key={a} className="chip ok">{a}</span>) : caps ? <span className="chip"><Cpu size={12} /> CPU only</span> : null}
        </div>
      </td>
      <td>
        {node.online && m ? (
          <div className="stack gap-2">
            <Meter name="CPU" value={m.cpu_percent} />
            {m.gpus[0] && <Meter name="GPU" value={m.gpus[0].utilization} />}
          </div>
        ) : (
          <span className="faint small">—</span>
        )}
      </td>
      <td className="mono">
        {active.length}/{node.max_concurrency}
        {node.paused && <span className="chip info ml-2">Paused</span>}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------

type Hardware = "cpu" | "nvidia" | "intel" | "amd";

const HW_HELP: Record<Hardware, string> = {
  cpu: "Works on any machine. Slower, but the best quality per megabyte.",
  nvidia: "GeForce/RTX/Quadro. Needs the NVIDIA driver and NVIDIA Container Toolkit on the host. AV1 encoding needs an RTX 40-series or newer.",
  intel: "Quick Sync on Intel CPUs with graphics, or Arc GPUs. The host must expose /dev/dri. AV1 needs Arc or Core Ultra.",
  amd: "Radeon GPUs through VA-API on Linux. The host must expose /dev/dri. AV1 needs RX 7000 series or newer.",
};

/** Shown with every connection snippet whose server address only works on the server itself. */
export function ServerUrlWarning({ url }: { url: string }) {
  if (!isLoopbackUrl(url)) return null;
  return (
    <Callout kind="warn" title="Other machines can't reach this server address">
      The snippet points nodes at <span className="mono">{url}</span>, which on another machine means that machine itself.
      This happens when you open FrameForge through localhost and{" "}
      <Link to="/settings">Settings → Server URL for nodes</Link> is empty. Set it to this server's LAN address (for
      example <span className="mono">http://192.168.1.20:8686</span>) and regenerate the token on the node's page, or
      replace the address in <span className="mono">FF_SERVER_URL</span> before you start the container.
    </Callout>
  );
}

export function AddNodeWizard({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [hardware, setHardware] = useState<Hardware>("nvidia");
  const [concurrency, setConcurrency] = useState<number | null>(1);
  const [mappings, setMappings] = useState<PathMapping[]>([]);
  const [result, setResult] = useState<NodeEnrollment | null>(null);
  const [tab, setTab] = useState<"compose" | "run">("compose");
  const [error, setError] = useState<string | null>(null);
  const create = useAction(
    () => api.post<NodeEnrollment>("/nodes", { name, hardware, max_concurrency: concurrency ?? 1, path_mappings: mappings.filter((m) => m.server && m.node) }),
    [["nodes"]],
  );
  const { data: nodes } = useNodes();
  const created = result?.node ? nodes?.find((n) => n.id === result.node!.id) : undefined;

  if (result) {
    return (
      <Modal
        wide
        title={`Connect “${name}”`}
        subtitle="Run this on the machine that will do the transcoding. The token is shown only once."
        onClose={onClose}
        footer={<button className="btn primary" onClick={onClose}>{created?.online ? "Done" : "Close"}</button>}
      >
        <div className="stack">
          {created?.online ? (
            <Callout kind="ok" title={`${name} is online`}>
              It reported {accelSummary(created).join(", ") || "CPU encoding only"}. It will start taking jobs right away.
            </Callout>
          ) : (
            <Callout kind="info" title="Waiting for the node to connect…">
              Start the container. It appears here automatically within a few seconds.
            </Callout>
          )}
          <ServerUrlWarning url={result.server_url} />
          <Tabs value={tab} onChange={setTab} tabs={[{ value: "compose", label: "docker-compose.yml" }, { value: "run", label: "docker run" }]} />
          <CopyBlock text={tab === "compose" ? result.compose : result.docker_run} />
          <div className="small muted">
            The node connects out to <span className="mono">{result.server_url}</span>. If that address isn't reachable from the other machine, set the public URL under Settings and regenerate the token. Media must be mounted at the same paths as on the server, or add path mappings on the node's page.
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      title="Add a node"
      subtitle="A node is any machine running the FrameForge container in node mode."
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn primary"
            disabled={!name || create.isPending}
            onClick={() => create.mutate(undefined, { onSuccess: (r) => setResult(r), onError: (e) => setError((e as Error).message) })}
          >
            Create node
          </button>
        </>
      }
    >
      <div className="stack">
        <Field label="Name">
          <input className="input" autoFocus placeholder="Editing-PC" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Hardware" help={<>{HW_HELP[hardware]} This only shapes the setup snippet; the node verifies what it can really do when it starts.</>}>
          <Segmented
            value={hardware}
            onChange={setHardware}
            options={[
              { value: "nvidia", label: "NVIDIA" },
              { value: "intel", label: "Intel" },
              { value: "amd", label: "AMD" },
              { value: "cpu", label: "CPU only" },
            ]}
          />
        </Field>
        <Field label="Jobs at the same time" help="1–2 is right for most GPUs. More rarely helps and can slow everything down.">
          <div className="w-num">
            <NumberInput value={concurrency} onChange={setConcurrency} min={1} max={16} />
          </div>
        </Field>
        <Field label="Path mappings (optional)" help="Only needed if this machine mounts your media at a different path than the server.">
          <PathMappingsEditor value={mappings} onChange={setMappings} />
        </Field>
        {error && <Callout kind="err" title="Couldn't create the node">{error}</Callout>}
      </div>
    </Modal>
  );
}

export function PathMappingsEditor({ value, onChange }: { value: PathMapping[]; onChange: (v: PathMapping[]) => void }) {
  return (
    <div className="stack gap-2">
      {value.map((m, i) => (
        <div className="row" key={i}>
          <input className="input mono" placeholder="Server path, e.g. /media" value={m.server} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, server: e.target.value } : x)))} />
          <span className="faint" aria-hidden>→</span>
          <input className="input mono" placeholder="Path on this node, e.g. /mnt/nas" value={m.node} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, node: e.target.value } : x)))} />
          <button className="btn icon ghost" title="Remove mapping" aria-label="Remove mapping" onClick={() => onChange(value.filter((_, j) => j !== i))}>
            <X size={16} />
          </button>
        </div>
      ))}
      <div>
        <button className="btn sm ghost" onClick={() => onChange([...value, { server: "", node: "" }])}>
          <Plus size={14} /> Add mapping
        </button>
      </div>
    </div>
  );
}
