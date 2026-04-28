import { useState, useCallback } from "react";
import { useNavigate, useLocation, Outlet } from "react-router-dom";
import {
  LayoutDashboard,
  AlertTriangle,
  Shield,
  Network,
  Play,
  Settings,
  LogOut,
  Menu,
  X,
  Sun,
  Moon,
  Monitor,
  Radio,
} from "lucide-react";
import { useTheme, type Theme } from "../hooks/useTheme";
import { logout } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import type { SSEEvent } from "../lib/types";

const NAV_ITEMS = [
  { path: "/", label: "Dashboard", icon: LayoutDashboard },
  { path: "/incidents", label: "Incidents", icon: AlertTriangle },
  { path: "/alerts", label: "Alerts", icon: Shield },
  { path: "/actions", label: "Actions", icon: Play },
  { path: "/topology", label: "Topology", icon: Network },
  { path: "/settings", label: "Settings", icon: Settings },
] as const;

const themeIcons: Record<Theme, typeof Sun> = {
  light: Sun,
  dark: Moon,
  midnight: Monitor,
};

const themeLabels: Record<Theme, string> = {
  light: "Light",
  dark: "Dark",
  midnight: "Midnight",
};

/**
 * App shell — sidebar + header + main content.
 *
 * Accessibility (ui-ux-pro-max priority 1):
 * - Skip-to-content link
 * - aria-current on active nav item
 * - Keyboard navigable sidebar
 *
 * Design (frontend-design):
 * - Industrial control room aesthetic
 * - Sidebar with subtle border glow
 * - Generous spacing, clear hierarchy
 */
export function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { theme, cycleTheme } = useTheme();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const ThemeIcon = themeIcons[theme];

  useSSE({
    onEvent: (_event: SSEEvent) => { /* each page handles its own SSE */ },
    onOpen: () => setSseConnected(true),
    onClose: () => setSseConnected(false),
  });

  const handleLogout = useCallback(async () => {
    try {
      await logout();
    } catch {
      // Ignore
    }
    navigate("/login");
  }, [navigate]);

  const handleNav = useCallback(
    (path: string) => {
      navigate(path);
      setMobileOpen(false);
    },
    [navigate]
  );

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-bg)]">
      {/* Skip link (accessibility priority 1) */}
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-transform duration-200 lg:static lg:translate-x-0 ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        role="navigation"
        aria-label="Main navigation"
      >
        {/* Brand */}
        <div className="flex h-14 items-center gap-3 border-b border-[var(--color-border)] px-4">
          <div className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-primary-muted)]">
            <Shield className="h-4 w-4 text-[var(--color-primary)]" />
          </div>
          <span className="text-sm font-bold tracking-tight text-[var(--color-text)]">Sentinel</span>
          <span className="rounded bg-[var(--color-surface-raised)] border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            v2.0
          </span>
          <button
            className="ml-auto rounded p-1.5 hover:bg-[var(--color-surface-raised)] lg:hidden cursor-pointer"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Nav items */}
        <nav className="flex-1 overflow-y-auto px-3 py-4">
          <div className="space-y-0.5">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive =
                item.path === "/"
                  ? location.pathname === "/"
                  : location.pathname.startsWith(item.path);
              return (
                <button
                  key={item.path}
                  onClick={() => handleNav(item.path)}
                  className={`group relative flex w-full items-center gap-3 rounded px-3 py-2 text-[13px] font-medium transition-all duration-150 cursor-pointer ${
                    isActive
                      ? "nav-active-stripe bg-[var(--color-primary-muted)] text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
                  }`}
                  aria-current={isActive ? "page" : undefined}
                >
                  <Icon
                    className={`h-[18px] w-[18px] shrink-0 transition-colors ${
                      isActive
                        ? "text-[var(--color-primary)]"
                        : "text-[var(--color-text-muted)] group-hover:text-[var(--color-text-secondary)]"
                    }`}
                  />
                  {item.label}
                </button>
              );
            })}
          </div>
        </nav>

        {/* Bottom actions */}
        <div className="border-t border-[var(--color-border)] p-3 space-y-0.5">
          {/* SSE live indicator */}
          <div className="sse-indicator">
            <Radio
              className={`h-[14px] w-[14px] shrink-0 ${sseConnected ? "text-[var(--severity-resolved)]" : "text-[var(--color-text-muted)]"}`}
            />
            <span>{sseConnected ? "SSE · live" : "SSE · connecting"}</span>
          </div>
          <button
            onClick={cycleTheme}
            className="flex w-full items-center gap-3 rounded px-3 py-2 text-[13px] font-medium text-[var(--color-text-muted)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)] transition-colors duration-150 cursor-pointer"
            aria-label={`Switch theme (current: ${themeLabels[theme]})`}
          >
            <ThemeIcon className="h-[18px] w-[18px] shrink-0" />
            {themeLabels[theme]}
          </button>
          <button
            onClick={handleLogout}
            className="flex w-full items-center gap-3 rounded px-3 py-2 text-[13px] font-medium text-[var(--color-text-muted)] hover:bg-[var(--severity-critical-bg)] hover:text-[var(--severity-critical)] transition-colors duration-150 cursor-pointer"
          >
            <LogOut className="h-[18px] w-[18px] shrink-0" />
            Logout
          </button>
        </div>
      </aside>

      {/* Main area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Mobile header */}
        <header className="flex h-14 items-center gap-3 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 lg:hidden">
          <button
            onClick={() => setMobileOpen(true)}
            className="rounded-md p-1.5 hover:bg-[var(--color-surface-raised)] cursor-pointer"
            aria-label="Open navigation"
          >
            <Menu className="h-5 w-5" />
          </button>
          <Shield className="h-4 w-4 text-[var(--color-primary)]" />
          <span className="text-sm font-bold tracking-tight">Sentinel</span>
        </header>

        {/* Content area — generous padding, max-width for reading comfort */}
        <main
          id="main-content"
          className="relative flex-1 overflow-y-auto"
        >
          <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
