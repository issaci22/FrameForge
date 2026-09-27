import { NavLink, Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Film, FolderOpen, LayoutDashboard, ListOrdered, LogOut, Menu as MenuIcon, Monitor, Moon, Server, SlidersHorizontal, Sun, Workflow, WifiOff, Settings as Cog } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { keys, useAuth, useOverview } from "../api/hooks";
import { disconnectLive, useLive } from "../live/events";
import { duration } from "../lib/format";
import { Logo } from "./ui";

type Theme = "system" | "dark" | "light";

function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("ff-theme") as Theme | null) ?? "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("ff-theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, setTheme] as const;
}

const THEME_NEXT: Record<Theme, Theme> = { dark: "light", light: "system", system: "dark" };
const THEME_LABEL: Record<Theme, string> = { dark: "Dark theme", light: "Light theme", system: "Theme follows your system" };

function NavItem({ to, icon, children, count, end }: { to: string; icon: ReactNode; children: ReactNode; count?: ReactNode; end?: boolean }) {
  return (
    <NavLink to={to} end={end}>
      {icon}
      <span className="flex-1 ellipsis">{children}</span>
      {count != null && <span className="nav-count">{count}</span>}
    </NavLink>
  );
}

export function AppShell() {
  const { data: overview } = useOverview();
  const { data: auth } = useAuth();
  const connected = useLive((s) => s.connected);
  const progress = useLive((s) => s.progress);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [theme, setTheme] = useTheme();
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => setNavOpen(false), [location.pathname]);

  const live = Object.values(progress);
  const active = Math.max(overview?.jobs.active ?? 0, live.length);
  const queued = overview?.jobs.queued ?? 0;
  const avg = live.length ? live.reduce((a, p) => a + (p.percent ?? 0), 0) / live.length : 0;
  const pending = (overview?.jobs.active ?? 0) + queued;
  const username = auth?.user?.username ?? "";

  const logout = async () => {
    await api.post("/auth/logout");
    disconnectLive();
    qc.setQueryData(keys.auth, { setup_required: false, authenticated: false, user: null });
    navigate("/login");
  };

  return (
    <div className={`shell ${navOpen ? "nav-open" : ""}`}>
      <header className="mobile-bar">
        <button className="btn ghost icon" aria-label="Open navigation" aria-expanded={navOpen} onClick={() => setNavOpen(true)}>
          <MenuIcon size={20} />
        </button>
        <Link to="/" aria-label="Dashboard">
          <Logo />
        </Link>
        <Link to="/queue" className="status" aria-label={`${active} transcoding, ${queued} queued`}>
          {!connected ? <WifiOff size={15} className="text-warn" /> : <span className={`live-dot ${active > 0 ? "on" : ""}`} />}
          <span className="mono">{active > 0 ? `${active} running` : queued ? `${queued} queued` : "Idle"}</span>
        </Link>
      </header>

      <div className="nav-scrim" onClick={() => setNavOpen(false)} />

      <aside className="sidebar" aria-label="Main navigation">
        <div className="sidebar-brand">
          <Link to="/" aria-label="Dashboard">
            <Logo />
          </Link>
        </div>
        <nav className="nav">
          <div className="nav-label">Overview</div>
          <NavItem to="/" end icon={<LayoutDashboard size={18} />}>
            Dashboard
          </NavItem>
          <NavItem to="/queue" icon={<ListOrdered size={18} />} count={pending > 0 ? pending : undefined}>
            Queue
          </NavItem>
          <div className="nav-label">Media</div>
          <NavItem to="/libraries" icon={<FolderOpen size={18} />}>
            Libraries
          </NavItem>
          <NavItem to="/files" icon={<Film size={18} />}>
            Files
          </NavItem>
          <div className="nav-label">Automation</div>
          <NavItem to="/rules" icon={<Workflow size={18} />}>
            Rules
          </NavItem>
          <NavItem to="/profiles" icon={<SlidersHorizontal size={18} />}>
            Profiles
          </NavItem>
          <div className="nav-label">System</div>
          <NavItem to="/nodes" icon={<Server size={18} />} count={overview ? `${overview.nodes.online}/${overview.nodes.total}` : undefined}>
            Nodes
          </NavItem>
          <NavItem to="/settings" icon={<Cog size={18} />}>
            Settings
          </NavItem>
        </nav>

        <Link to="/queue" className="activity" aria-live="polite">
          <div className="activity-head">
            <span className={`live-dot ${active > 0 ? "on" : ""}`} />
            {active > 0 ? `${active} transcoding` : "Nothing running"}
            {active > 0 && overview?.eta_seconds != null && <span className="eta">{duration(overview.eta_seconds)}</span>}
          </div>
          {active > 0 && (
            <div className="bar" aria-hidden>
              <i style={{ width: `${Math.min(100, avg)}%` }} />
            </div>
          )}
          <span className="caption">{queued ? `${queued} waiting in the queue` : "Queue is empty"}</span>
          {!connected && (
            <span className="offline" title="Live updates are reconnecting">
              <WifiOff size={13} /> Reconnecting…
            </span>
          )}
        </Link>

        <div className="sidebar-user">
          <span className="avatar" aria-hidden>
            {username.slice(0, 1) || "?"}
          </span>
          <div className="who">
            <b className="ellipsis">{username}</b>
            <span>{auth?.user?.is_admin ? "Administrator" : "User"}</span>
          </div>
          <button className="btn ghost icon sm" title={`${THEME_LABEL[theme]} (click to change)`} aria-label={THEME_LABEL[theme]} onClick={() => setTheme(THEME_NEXT[theme])}>
            {theme === "light" ? <Sun size={16} /> : theme === "dark" ? <Moon size={16} /> : <Monitor size={16} />}
          </button>
          <button className="btn ghost icon sm" title="Sign out" aria-label="Sign out" onClick={logout}>
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
