import { useMemo, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../../api/client";
import { keys, useDebounced } from "../../api/hooks";
import type { User } from "../../api/types";
import { buildTasks, type Task } from "./commit";
import { PASSWORD_LENGTH, USERNAME_LENGTH, USERNAME_PATTERN, type FolderState, type ServerData, type SetupDraft } from "./model";

export { useDebounced, useProfilePreview } from "../../api/hooks";

async function checkFolder(path: string): Promise<FolderState> {
  try {
    const listing = await api.get<{ dirs: unknown[] }>(`/system/browse?path=${encodeURIComponent(path)}`);
    return { state: "found", dirs: listing.dirs.length };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return { state: "missing" };
    if (e instanceof ApiError && e.status === 403) return { state: "denied" };
    return { state: "unknown", message: (e as Error).message };
  }
}

/** Does each folder exist inside the container? Uses the same endpoint as the folder picker. */
export function useFolderChecks(paths: string[], enabled: boolean): Record<string, FolderState> {
  const joined = useDebounced(paths.join("\n"), 400);
  const list = joined ? joined.split("\n") : [];
  const results = useQueries({
    queries: list.map((p) => ({ queryKey: ["setup", "folder", p], queryFn: () => checkFolder(p), enabled, staleTime: 30_000, retry: false })),
  });
  const out: Record<string, FolderState> = {};
  for (const p of paths) out[p] = { state: "checking" };
  list.forEach((p, i) => {
    if (results[i]?.data && paths.includes(p)) out[p] = results[i].data!;
  });
  return out;
}

export function useAdminForm(onCreated: (user: User) => void) {
  const qc = useQueryClient();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checks = {
    userLength: username.length >= USERNAME_LENGTH.min && username.length <= USERNAME_LENGTH.max,
    userChars: USERNAME_PATTERN.test(username),
    passwordLength: password.length >= PASSWORD_LENGTH.min && password.length <= PASSWORD_LENGTH.max,
    match: confirm.length > 0 && confirm === password,
  };
  const valid = Object.values(checks).every(Boolean);

  const submit = async () => {
    setTouched(true);
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ user: User }>("/auth/setup", { username, password });
      setPassword("");
      setConfirm("");
      onCreated(res.user);
    } catch (e) {
      setError((e as Error).message);
      // Someone else finished setup first: the router will send this browser to the sign-in page.
      if (e instanceof ApiError && e.status === 409) window.setTimeout(() => qc.invalidateQueries({ queryKey: keys.auth }), 2500);
    } finally {
      setBusy(false);
    }
  };

  return { username, setUsername, password, setPassword, confirm, setConfirm, touched, setTouched, busy, error, checks, valid, submit };
}

export type AdminForm = ReturnType<typeof useAdminForm>;

export type TaskStatus = { state: "running" | "done" | "failed"; error?: string; note?: string };

// Creating the same library or rule twice would fail (or duplicate it), so those are remembered across retries.
// Updates (settings, profiles, nodes, aging policy, evaluate) are idempotent and simply run again.
const REMEMBER = /^(library|rule):/;

export function useCommit(draft: SetupDraft, server: ServerData, update: (fn: (d: SetupDraft) => SetupDraft) => void, onSuccess: (warnings: string[]) => void) {
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<Record<string, TaskStatus>>({});
  const tasks: Task[] = useMemo(() => buildTasks(draft, server), [draft, server]);
  const failed = Object.values(status).some((s) => s.state === "failed");
  const done = tasks.filter((t) => status[t.id]?.state === "done" || t.id in draft.committed).length;

  const run = async () => {
    if (running) return;
    setRunning(true);
    setStatus({});
    let created = { ...draft.committed };
    const warnings: string[] = [];
    for (const t of tasks) {
      if (t.id in created) {
        setStatus((s) => ({ ...s, [t.id]: { state: "done", note: "Already created" } }));
        continue;
      }
      setStatus((s) => ({ ...s, [t.id]: { state: "running" } }));
      try {
        const r = await t.run(created);
        if (REMEMBER.test(t.id)) {
          created = { ...created, [t.id]: r.id ?? true };
          update((d) => ({ ...d, committed: { ...d.committed, [t.id]: r.id ?? true } }));
        }
        warnings.push(...(r.warnings ?? []));
        setStatus((s) => ({ ...s, [t.id]: { state: "done", note: r.note } }));
      } catch (e) {
        setStatus((s) => ({ ...s, [t.id]: { state: "failed", error: (e as Error).message } }));
        setRunning(false);
        return;
      }
    }
    setRunning(false);
    onSuccess(warnings);
  };

  return { tasks, status, running, failed, done, run };
}

export type Commit = ReturnType<typeof useCommit>;
