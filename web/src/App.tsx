import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { keys, useAuth } from "./api/hooks";
import { connectLive } from "./live/events";
import { AppShell } from "./components/AppShell";
import { ErrorState, Spinner } from "./components/ui";
import { LoginPage } from "./pages/Auth";
import { SetupPage } from "./pages/setup/SetupPage";
import { DashboardPage } from "./pages/Dashboard";
import { QueuePage } from "./pages/Queue";
import { JobDetailPage } from "./pages/JobDetail";
import { LibrariesPage } from "./pages/Libraries";
import { FilesPage } from "./pages/Files";
import { NodesPage } from "./pages/Nodes";
import { NodeDetailPage } from "./pages/NodeDetail";
import { ProfilesPage } from "./pages/Profiles";
import { RulesPage } from "./pages/Rules";
import { SettingsPage } from "./pages/Settings";

export function App() {
  const { data: auth, isLoading, error, refetch } = useAuth();
  const qc = useQueryClient();
  const location = useLocation();

  useEffect(() => {
    const onUnauthorized = () => qc.invalidateQueries({ queryKey: keys.auth });
    window.addEventListener("ff:unauthorized", onUnauthorized);
    return () => window.removeEventListener("ff:unauthorized", onUnauthorized);
  }, [qc]);

  useEffect(() => {
    if (auth?.authenticated) connectLive(qc);
  }, [auth?.authenticated, qc]);

  if (isLoading) return <Spinner />;
  if (!auth) return <ErrorState title="Can't reach the FrameForge server" error={error} onRetry={() => refetch()} />;

  if (auth.setup_required) {
    return (
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="*" element={<Navigate to="/setup" replace />} />
      </Routes>
    );
  }
  if (!auth.authenticated) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname }} />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/setup" element={<SetupPage />} />
      <Route element={<AppShell />}>
        <Route index element={<DashboardPage />} />
        <Route path="queue" element={<QueuePage />} />
        <Route path="jobs/:id" element={<JobDetailPage />} />
        <Route path="libraries" element={<LibrariesPage />} />
        <Route path="files" element={<FilesPage />} />
        <Route path="nodes" element={<NodesPage />} />
        <Route path="nodes/:id" element={<NodeDetailPage />} />
        <Route path="profiles" element={<ProfilesPage />} />
        <Route path="rules" element={<RulesPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
