import { useEffect, useState } from "react";
import {
  HashRouter,
  Routes,
  Route,
  Navigate,
  useNavigate,
} from "react-router-dom";
import { lazy, Suspense } from "react";
import { Layout } from "./components/Layout";
import { Login } from "./pages/Login";
import { Setup } from "./pages/Setup";
import { Dashboard } from "./pages/Dashboard";
import { Incidents } from "./pages/Incidents";
import { IncidentDetail } from "./pages/IncidentDetail";
import { Alerts } from "./pages/Alerts";
import { SettingsPage } from "./pages/SettingsPage";
import { checkSession } from "./lib/api";

// Lazy-load Topology (pulls in React Flow + dagre — ~160KB gzipped)
const Topology = lazy(() =>
  import("./pages/Topology").then((m) => ({ default: m.Topology }))
);

/**
 * Auth guard — checks session before rendering protected routes.
 * Redirects to /login if not authenticated, or /setup if no password set.
 */
function AuthGuard({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const [checked, setChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    checkSession()
      .then((res) => {
        setAuthenticated(res.authenticated);
        if (!res.authenticated) {
          if (res.reason === "needs_setup") {
            navigate("/setup", { replace: true });
          } else {
            navigate("/login", { replace: true });
          }
        }
      })
      .catch(() => {
        navigate("/login", { replace: true });
      })
      .finally(() => setChecked(true));
  }, [navigate]);

  if (!checked) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-primary)] border-t-transparent" />
      </div>
    );
  }

  if (!authenticated) return null;
  return <>{children}</>;
}

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/setup" element={<Setup />} />
        <Route
          element={
            <AuthGuard>
              <Layout />
            </AuthGuard>
          }
        >
          <Route path="/" element={<Dashboard />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/incidents/:id" element={<IncidentDetail />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/topology" element={<Suspense fallback={<div className="flex h-64 items-center justify-center"><div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-primary)] border-t-transparent" /></div>}><Topology /></Suspense>} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}
