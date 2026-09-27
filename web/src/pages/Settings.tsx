import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Info, KeyRound, RefreshCw, ScrollText, SlidersHorizontal } from "lucide-react";
import { api } from "../api/client";
import { useAction, useSettings, useSystem } from "../api/hooks";
import type { GeneralSettings } from "../api/types";
import { Callout, ErrorState, Field, LoadingState, NumberInput, PageHeader, Section, Segmented, SettingRow, Toggle, useToast } from "../components/ui";

type Tab = "general" | "account" | "system" | "logs";

const TABS: { value: Tab; label: string; icon: React.ReactNode }[] = [
  { value: "general", label: "General", icon: <SlidersHorizontal size={16} /> },
  { value: "account", label: "Account", icon: <KeyRound size={16} /> },
  { value: "system", label: "About", icon: <Info size={16} /> },
  { value: "logs", label: "Server log", icon: <ScrollText size={16} /> },
];

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = requested && TABS.some((t) => t.value === requested) ? requested : "general";
  const setTab = (t: Tab) => setParams(t === "general" ? {} : { tab: t }, { replace: true });

  return (
    <div className="page">
      <PageHeader title="Settings" description="Server-wide options. Library, rule, profile and node settings live on their own pages." />
      <div className="settings-layout">
        <nav className="settings-nav" role="tablist" aria-label="Settings sections">
          {TABS.map((t) => (
            <button key={t.value} type="button" role="tab" aria-selected={tab === t.value} className={tab === t.value ? "on" : ""} onClick={() => setTab(t.value)}>
              {t.icon}
              {t.label}
            </button>
          ))}
        </nav>
        <div className="settings-content">
          {tab === "general" && <GeneralTab />}
          {tab === "account" && <AccountTab />}
          {tab === "system" && <SystemTab />}
          {tab === "logs" && <LogsTab />}
        </div>
      </div>
    </div>
  );
}

function GeneralTab() {
  const { data, error, refetch } = useSettings();
  const [s, setS] = useState<GeneralSettings | null>(null);
  const toast = useToast();
  useEffect(() => {
    if (data) setS(data);
  }, [data]);
  const save = useAction((body: GeneralSettings) => api.put<GeneralSettings>("/settings", body), [["settings"]]);
  if (error && !data) return <ErrorState title="Couldn't load settings" error={error} onRetry={() => refetch()} />;
  if (!s) return <LoadingState rows={2} />;
  const qh = s.quiet_hours;
  const dirty = JSON.stringify(s) !== JSON.stringify(data);

  return (
    <div className="stack gap-6">
      <Section title="Background work hours" description="Keep low-priority conversions to the hours you don't use the machines.">
        <div className="settings-list">
          <SettingRow label="Limit low-priority jobs to certain hours" description="Jobs you queue manually always run immediately.">
            <Toggle checked={qh.enabled} onChange={(v) => setS({ ...s, quiet_hours: { ...qh, enabled: v } })} />
          </SettingRow>
          <SettingRow label="Applies to" dim={!qh.enabled}>
            <Segmented
              value={qh.applies_to}
              onChange={(v) => setS({ ...s, quiet_hours: { ...qh, applies_to: v } })}
              options={[
                { value: "background", label: "Background jobs" },
                { value: "low_and_below", label: "Low + Background" },
              ]}
            />
          </SettingRow>
          <SettingRow label="Allowed between" description="Uses the server's time zone (the TZ variable in docker-compose.yml)." dim={!qh.enabled}>
            <input className="input mono w-time" type="time" aria-label="Start" value={qh.window.start} onChange={(e) => setS({ ...s, quiet_hours: { ...qh, window: { ...qh.window, start: e.target.value } } })} />
            <span className="faint">and</span>
            <input className="input mono w-time" type="time" aria-label="End" value={qh.window.end} onChange={(e) => setS({ ...s, quiet_hours: { ...qh, window: { ...qh.window, end: e.target.value } } })} />
          </SettingRow>
        </div>
      </Section>

      <Section title="Jobs & scanning">
        <div className="settings-list">
          <SettingRow label="Automatic retries" description="For temporary problems such as a busy GPU encoder.">
            <div className="w-num">
              <NumberInput value={s.max_attempts} onChange={(v) => setS({ ...s, max_attempts: v ?? 2 })} min={1} max={10} suffix="attempts" />
            </div>
          </SettingRow>
          <SettingRow label="Parallel file analysis" description="How many files are probed at once during scans.">
            <div className="w-num-sm">
              <NumberInput value={s.probe_concurrency} onChange={(v) => setS({ ...s, probe_concurrency: v ?? 4 })} min={1} max={32} />
            </div>
          </SettingRow>
          <SettingRow label="Keep job history for" description="Finished jobs older than this are removed from the history.">
            <div className="w-num">
              <NumberInput value={s.job_history_days} onChange={(v) => setS({ ...s, job_history_days: v ?? 180 })} min={7} max={3650} suffix="days" />
            </div>
          </SettingRow>
        </div>
      </Section>

      <Section title="Nodes">
        <div className="settings-list">
          <SettingRow top label="Server URL for nodes" description="Used in Add Node snippets, e.g. http://192.168.1.20:8686. Leave empty to use the address you're browsing from.">
            <input className="input mono w-wide" aria-label="Server URL for nodes" value={s.public_url ?? ""} onChange={(e) => setS({ ...s, public_url: e.target.value || null })} placeholder="http://server:8686" />
          </SettingRow>
        </div>
      </Section>

      <div className={`save-bar ${dirty ? "show" : ""}`} aria-hidden={!dirty}>
        <span className="muted small">You have unsaved changes.</span>
        <span className="spacer" />
        <button className="btn" disabled={!dirty} onClick={() => data && setS(data)}>
          Discard
        </button>
        <button className="btn primary" disabled={!dirty || save.isPending} onClick={() => save.mutate(s, { onSuccess: () => toast("Settings saved"), onError: (e) => toast((e as Error).message, "err") })}>
          Save settings
        </button>
      </div>
    </div>
  );
}

function AccountTab() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const change = useAction(() => api.post("/auth/password", { current_password: current, new_password: next }), []);
  return (
    <Section title="Change password" description="Other signed-in sessions are signed out when the password changes.">
      <form
        className="stack account-form"
        onSubmit={(e) => {
          e.preventDefault();
          change.mutate(undefined, {
            onSuccess: () => {
              setMsg({ kind: "ok", text: "Password changed" });
              setCurrent("");
              setNext("");
            },
            onError: (e) => setMsg({ kind: "err", text: (e as Error).message }),
          });
        }}
      >
        <Field label="Current password" htmlFor="pw-current">
          <input id="pw-current" className="input" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
        </Field>
        <Field label="New password" htmlFor="pw-new" help="At least 8 characters.">
          <input id="pw-new" className="input" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
        </Field>
        {msg && <Callout kind={msg.kind} title={msg.text} />}
        <div>
          <button type="submit" className="btn primary" disabled={!current || next.length < 8 || change.isPending}>
            Change password
          </button>
        </div>
      </form>
    </Section>
  );
}

function SystemTab() {
  const { data, error, refetch } = useSystem();
  if (error && !data) return <ErrorState title="Couldn't load server information" error={error} onRetry={() => refetch()} />;
  if (!data) return <LoadingState rows={1} />;
  return (
    <Section title="About this server">
      <dl className="kv">
        <dt>FrameForge</dt>
        <dd>{data.version}</dd>
        <dt>Role</dt>
        <dd>{data.role === "all" ? "Server + built-in node" : data.role}</dd>
        <dt>Database</dt>
        <dd>{data.database === "sqlite" ? "SQLite (in /config)" : "PostgreSQL"}</dd>
        <dt>FFmpeg</dt>
        <dd className="small">{data.ffmpeg}</dd>
        <dt>HandBrake</dt>
        <dd className="faint">{data.engines.handbrake?.reason ?? "unavailable"}</dd>
        <dt>Config folder</dt>
        <dd className="mono">{data.config_dir}</dd>
        <dt>Platform</dt>
        <dd className="small">{data.platform}</dd>
        <dt>API</dt>
        <dd>
          <a href="/api/docs" target="_blank" rel="noreferrer" className="link">
            /api/docs
          </a>{" "}
          <span className="faint">(interactive OpenAPI reference)</span>
        </dd>
      </dl>
    </Section>
  );
}

function LogsTab() {
  const { data, refetch, isFetching } = useQuery({ queryKey: ["server-log"], queryFn: () => api.text("/system/logs?lines=800") });
  return (
    <Section
      title="server.log"
      description="The last 800 lines."
      actions={
        <button className="btn sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw size={14} className={isFetching ? "spin" : undefined} /> Refresh
        </button>
      }
    >
      <pre className="log tall">{data || "Empty"}</pre>
    </Section>
  );
}
