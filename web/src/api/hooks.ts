import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api, qs } from "./client";
import type {
  AgingPolicy,
  AuthStatus,
  FieldDef,
  FileDetail,
  GeneralSettings,
  HistoryDay,
  JobDetail,
  JobList,
  Library,
  MediaFile,
  Node,
  Overview,
  Profile,
  ProfileOptions,
  ProfilePreview,
  ProfileSpec,
  RetainedOriginal,
  Rule,
  SystemInfo,
} from "./types";

export const keys = {
  auth: ["auth"] as const,
  overview: ["stats", "overview"] as const,
  history: (days: number) => ["stats", "history", days] as const,
  jobs: (params: Record<string, unknown>) => ["jobs", params] as const,
  job: (id: number) => ["job", id] as const,
  libraries: ["libraries"] as const,
  library: (id: number) => ["library", id] as const,
  files: (params: Record<string, unknown>) => ["files", params] as const,
  file: (id: number) => ["file", id] as const,
  nodes: ["nodes"] as const,
  node: (id: number) => ["node", id] as const,
  profiles: ["profiles"] as const,
  profileOptions: ["profiles", "options"] as const,
  retention: (libraryId: number) => ["retention", libraryId] as const,
  rules: ["rules"] as const,
  ruleFields: ["rules", "fields"] as const,
  aging: (libraryId: number | null) => ["aging", libraryId] as const,
  settings: ["settings"] as const,
  system: ["system"] as const,
};

export const useAuth = () => useQuery({ queryKey: keys.auth, queryFn: () => api.get<AuthStatus>("/auth/status"), staleTime: 60_000 });
export const useOverview = () => useQuery({ queryKey: keys.overview, queryFn: () => api.get<Overview>("/stats/overview"), refetchInterval: 30_000 });
export const useHistory = (days = 30) => useQuery({ queryKey: keys.history(days), queryFn: () => api.get<HistoryDay[]>(`/stats/history?days=${days}`) });

export const useJobs = (params: { group?: string; library_id?: number; q?: string; limit?: number; offset?: number }) =>
  useQuery({ queryKey: keys.jobs(params), queryFn: () => api.get<JobList>(`/jobs${qs(params)}`), placeholderData: (prev) => prev });
export const useJob = (id: number) => useQuery({ queryKey: keys.job(id), queryFn: () => api.get<JobDetail>(`/jobs/${id}`) });

// `enabled` lets the setup wizard hold these back until the first admin session exists.
export const useLibraries = (enabled = true) => useQuery({ queryKey: keys.libraries, queryFn: () => api.get<Library[]>("/libraries"), enabled });
export const useFiles = (params: { library_id?: number; status?: string; codec?: string; q?: string; sort?: string; order?: string; offset?: number; limit?: number }) =>
  useQuery({ queryKey: keys.files(params), queryFn: () => api.get<{ total: number; items: MediaFile[] }>(`/files${qs(params)}`), placeholderData: (prev) => prev });
export const useFile = (id: number | null) =>
  useQuery({ queryKey: keys.file(id ?? 0), queryFn: () => api.get<FileDetail>(`/files/${id}`), enabled: id != null });

export const useNodes = (enabled = true) => useQuery({ queryKey: keys.nodes, queryFn: () => api.get<Node[]>("/nodes"), enabled });
export const useNode = (id: number) => useQuery({ queryKey: keys.node(id), queryFn: () => api.get<Node>(`/nodes/${id}`) });

export const useProfiles = (enabled = true) => useQuery({ queryKey: keys.profiles, queryFn: () => api.get<Profile[]>("/profiles"), enabled });
// Formats, starting points and which encoders online nodes verified. Hardware changes arrive as node events.
export const useProfileOptions = (enabled = true) =>
  useQuery({ queryKey: keys.profileOptions, queryFn: () => api.get<ProfileOptions>("/profiles/options"), staleTime: 30_000, enabled });
export const useRetained = (libraryId: number, enabled = true) =>
  useQuery({ queryKey: keys.retention(libraryId), queryFn: () => api.get<RetainedOriginal[]>(`/retention${qs({ library_id: libraryId })}`), enabled });

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Advice, native quality values and each online node's encoder for a draft spec (a dry run; nothing is encoded). */
export function useProfilePreview(spec: ProfileSpec | null, enabled = true) {
  const key = useDebounced(spec ? JSON.stringify(spec) : "", 250);
  return useQuery({
    queryKey: ["profiles", "preview", key],
    queryFn: () => api.post<ProfilePreview>("/profiles/preview", { spec: JSON.parse(key) }),
    enabled: enabled && !!key,
    placeholderData: (prev) => prev,
    retry: false,
    staleTime: 30_000,
  });
}

export const useRules = (enabled = true) => useQuery({ queryKey: keys.rules, queryFn: () => api.get<Rule[]>("/rules"), enabled });
export const useRuleFields = (enabled = true) =>
  useQuery({ queryKey: keys.ruleFields, queryFn: () => api.get<{ fields: FieldDef[]; priorities: { value: number; label: string }[] }>("/rules/fields"), staleTime: Infinity, enabled });
export const useAging = (libraryId: number | null) =>
  useQuery({ queryKey: keys.aging(libraryId), queryFn: () => api.get<{ policy: AgingPolicy | null }>(`/rules/aging/policy${qs({ library_id: libraryId })}`) });

export const useSettings = (enabled = true) => useQuery({ queryKey: keys.settings, queryFn: () => api.get<GeneralSettings>("/settings"), enabled });
export const useSystem = (enabled = true) => useQuery({ queryKey: keys.system, queryFn: () => api.get<SystemInfo>("/system/info"), staleTime: 300_000, enabled });

/** Mutation that invalidates the given query keys on success. */
export function useAction<TVars, TResult = unknown>(fn: (vars: TVars) => Promise<TResult>, invalidate: QueryKey[] = []) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      for (const key of invalidate) qc.invalidateQueries({ queryKey: key });
    },
  });
}
