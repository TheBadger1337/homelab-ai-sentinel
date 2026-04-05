import { useState, useEffect, useCallback } from "react";

export type Theme = "dark" | "light" | "midnight";

const STORAGE_KEY = "sentinel-theme";
const DEFAULT_THEME: Theme = "dark";

function getStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "dark" || stored === "light" || stored === "midnight") {
      return stored;
    }
  } catch {
    // localStorage unavailable
  }
  return DEFAULT_THEME;
}

/**
 * Theme hook — reads from localStorage, applies data-theme attribute.
 * Dark by default (homelab aesthetic, consistent with Grafana/Portainer).
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getStoredTheme);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Ignore
    }
  }, [theme]);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
  }, []);

  const cycleTheme = useCallback(() => {
    setThemeState((current) => {
      const order: Theme[] = ["dark", "light", "midnight"];
      const idx = order.indexOf(current);
      return order[(idx + 1) % order.length];
    });
  }, []);

  return { theme, setTheme, cycleTheme };
}
