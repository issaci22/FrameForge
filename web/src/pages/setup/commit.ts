// Complete Setup: the existing endpoints, called in an order that keeps references valid.
// Libraries exist before the rules that point at them; exception rules are created before the aging
// policy so they're checked first (new rules and new policies are appended to the end of the rule list).

import { api } from "../../api/client";
import { storageBody, usesBackupFolder, usesOutputFolder } from "../../lib/storage";
import type { Library } from "../../api/types";
import {
  effectivePublicUrl,
  effectiveQuietHours,
  effectiveSpec,
  nodeChanged,
  nodeDraft,
  nodePatch,
  normPath,
  settingsChanged,
  specChanged,
  type ServerData,
  type SetupDraft,
} from "./model";

export interface TaskResult {
  id?: number;
  warnings?: string[];
  note?: string;
}

export interface Task {
  id: string;
  label: string;
  endpoint: string;
  run: (created: SetupDraft["committed"]) => Promise<TaskResult>;
}

const libId = (created: SetupDraft["committed"], key: string): number => {
  const id = created[`library:${key}`];
  if (typeof id !== "number") throw new Error("Its library wasn't created");
  return id;
};

export function buildTasks(d: SetupDraft, server: ServerData): Task[] {
  const tasks: Task[] = [];

  if (server.settings && settingsChanged(d, server.settings)) {
    const s = server.settings;
    tasks.push({
      id: "settings",
      label: "Save general settings",
      endpoint: "PUT /settings",
      run: async () => {
        await api.put("/settings", { ...s, public_url: effectivePublicUrl(d, s).trim() || null, quiet_hours: effectiveQuietHours(d, s) });
        return {};
      },
    });
  }

  for (const p of server.profiles ?? []) {
    const spec = effectiveSpec(d, p);
    if (!specChanged(spec, p.spec)) continue;
    tasks.push({
      id: `profile:${p.id}`,
      label: `Update profile “${p.name}”`,
      endpoint: `PUT /profiles/${p.id}`,
      run: async () => {
        await api.put(`/profiles/${p.id}`, { name: p.name, description: p.description, spec });
        return {};
      },
    });
  }

  for (const n of server.nodes ?? []) {
    if (!nodeChanged(d, n)) continue;
    tasks.push({
      id: `node:${n.id}`,
      label: `Configure node “${n.name}”`,
      endpoint: `PATCH /nodes/${n.id}`,
      run: async () => {
        await api.patch(`/nodes/${n.id}`, nodePatch(nodeDraft(d, n)));
        return {};
      },
    });
  }

  for (const lib of d.libraries) {
    tasks.push({
      id: `library:${lib.key}`,
      label: `Create library “${lib.name.trim()}” and start its first scan`,
      endpoint: "POST /libraries",
      run: async () => {
        const created = await api.post<Library>("/libraries", {
          name: lib.name.trim(),
          paths: lib.paths.map((p) => p.trim()).filter(Boolean).map(normPath),
          enabled: true,
          automation_enabled: d.automation === "auto",
          scan_interval_minutes: lib.scan_interval_minutes,
          exclude_patterns: lib.exclude_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
          ...storageBody(lib.storage),
          output_path: usesOutputFolder(lib.storage) ? lib.output_path.trim() || null : null,
          backup_path: usesBackupFolder(lib.storage) ? lib.backup_path.trim() || null : null,
          validation: lib.validation,
        });
        return { id: created.id, warnings: created.warnings };
      },
    });
  }

  for (const r of d.exceptions) {
    const scope = r.library_key ? d.libraries.find((l) => l.key === r.library_key) : null;
    tasks.push({
      id: `rule:${r.key}`,
      label: `Create rule “${r.name.trim()}”${scope ? ` for “${scope.name.trim()}”` : ""}`,
      endpoint: "POST /rules",
      run: async (created) => {
        const rule = await api.post<{ id: number }>("/rules", {
          name: r.name.trim(),
          description: "Created during first-time setup",
          library_id: r.library_key ? libId(created, r.library_key) : null,
          enabled: true,
          conditions: { type: "group", op: "all", children: r.conditions },
          action: r.action,
          profile_id: r.action === "transcode" ? r.profile_id : null,
          priority: r.priority,
          schedule: {},
          skip_if_target_codec: true,
        });
        return { id: rule.id };
      },
    });
  }

  const agingTargets = d.aging.enabled ? d.libraries.filter((l) => !d.aging.excluded.includes(l.key)) : [];
  for (const lib of agingTargets) {
    tasks.push({
      id: `aging:${lib.key}`,
      label: `Save the aging policy for “${lib.name.trim()}”`,
      endpoint: "PUT /rules/aging/policy",
      run: async (created) => {
        await api.put("/rules/aging/policy", {
          library_id: libId(created, lib.key),
          age_field: d.aging.age_field,
          stages: [...d.aging.stages].sort((a, b) => a.min_days - b.min_days).map((s) => ({ ...s, profile_id: s.action === "transcode" ? s.profile_id : null })),
          window: d.policyWindow,
          enabled: true,
        });
        return {};
      },
    });
  }

  if (agingTargets.length > 0 || d.exceptions.length > 0) {
    for (const lib of d.libraries) {
      tasks.push({
        id: `evaluate:${lib.key}`,
        label: `Apply the rules to “${lib.name.trim()}”`,
        endpoint: "POST /libraries/{id}/evaluate",
        run: async (created) => {
          const id = libId(created, lib.key);
          // A running scan evaluates the rules itself when it finishes. Evaluating only after a scan that
          // already ended covers small folders that finished before their rules existed.
          const current = await api.get<Library>(`/libraries/${id}`);
          if (current.scanning) return { note: "First scan still running; it applies the rules when it finishes" };
          const r = await api.post<{ jobs_created: number }>(`/libraries/${id}/evaluate`);
          return { note: d.automation === "auto" ? `${r.jobs_created} job(s) queued` : "Decisions recorded; automation is off, so nothing was queued" };
        },
      });
    }
  }

  return tasks;
}
