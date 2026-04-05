import { useState, useCallback, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Shield, Eye, EyeOff } from "lucide-react";
import { Button } from "../components/Button";
import { setup } from "../lib/api";
import { useTheme } from "../hooks/useTheme";

/**
 * First-run setup page — create a UI password.
 *
 * Shown when the backend returns needs_setup (DB available but no password set).
 * After successful setup, the user is auto-logged in and redirected to dashboard.
 */
export function Setup() {
  const navigate = useNavigate();
  const { theme, cycleTheme } = useTheme();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      setError("");

      if (password !== confirm) {
        setError("Passwords do not match");
        return;
      }
      if (password.length < 8) {
        setError("Password must be at least 8 characters");
        return;
      }

      setLoading(true);
      try {
        await setup(password);
        navigate("/", { replace: true });
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Setup failed"
        );
      } finally {
        setLoading(false);
      }
    },
    [password, confirm, navigate]
  );

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse at 50% 30%, var(--color-primary-muted) 0%, transparent 60%)",
          opacity: 0.6,
        }}
        aria-hidden="true"
      />

      <div className="relative w-full max-w-sm">
        <div className="mb-10 flex flex-col items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[var(--color-primary-muted)] shadow-lg">
            <Shield className="h-7 w-7 text-[var(--color-primary)]" />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-bold tracking-tight">Sentinel</h1>
            <p className="mt-1.5 text-[13px] font-medium uppercase tracking-widest text-[var(--color-text-muted)]">
              First-Run Setup
            </p>
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 shadow-xl"
        >
          <p className="mb-6 text-sm text-[var(--color-text-secondary)]">
            Create a password to secure the Sentinel dashboard. This password
            is hashed and stored in the local database.
          </p>

          <div className="mb-4">
            <label
              htmlFor="password"
              className="mb-2 block text-[13px] font-semibold text-[var(--color-text-secondary)]"
            >
              Password
            </label>
            <div className="relative">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-4 pr-10 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] transition-all duration-150 focus:border-[var(--color-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring)]"
                placeholder="At least 8 characters"
                autoFocus
                autoComplete="new-password"
                required
                minLength={8}
              />
              <button
                type="button"
                className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)] cursor-pointer"
                onClick={() => setShowPassword(!showPassword)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          <div className="mb-6">
            <label
              htmlFor="confirm"
              className="mb-2 block text-[13px] font-semibold text-[var(--color-text-secondary)]"
            >
              Confirm Password
            </label>
            <input
              id="confirm"
              type={showPassword ? "text" : "password"}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-4 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] transition-all duration-150 focus:border-[var(--color-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring)]"
              placeholder="Re-enter password"
              autoComplete="new-password"
              required
              minLength={8}
            />
          </div>

          {error && (
            <p
              className="mb-4 text-[13px] font-medium text-[var(--severity-critical)]"
              role="alert"
            >
              {error}
            </p>
          )}

          <Button type="submit" loading={loading} className="w-full">
            Create Password
          </Button>
        </form>

        <div className="mt-6 flex justify-center">
          <button
            onClick={cycleTheme}
            className="rounded-md px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors cursor-pointer"
          >
            Theme: {theme}
          </button>
        </div>
      </div>
    </div>
  );
}
