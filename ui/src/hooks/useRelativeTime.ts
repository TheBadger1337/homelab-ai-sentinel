import { useState, useEffect } from "react";

const INTERVALS = [
  { label: "y", seconds: 31536000 },
  { label: "mo", seconds: 2592000 },
  { label: "d", seconds: 86400 },
  { label: "h", seconds: 3600 },
  { label: "m", seconds: 60 },
  { label: "s", seconds: 1 },
] as const;

/**
 * Format a Unix timestamp as relative time ("5m ago", "2h ago").
 * Updates every 30 seconds via requestAnimationFrame.
 */
export function formatRelativeTime(ts: number): string {
  if (!ts) return "—";
  const now = Date.now() / 1000;
  const diff = Math.max(0, now - ts);

  if (diff < 5) return "just now";

  for (const interval of INTERVALS) {
    const count = Math.floor(diff / interval.seconds);
    if (count >= 1) {
      return `${count}${interval.label} ago`;
    }
  }
  return "just now";
}

/**
 * Format a duration in seconds as human-readable ("47m", "2h 15m").
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/**
 * Format Unix timestamp as full ISO string for tooltips.
 */
export function formatTimestamp(ts: number): string {
  if (!ts) return "";
  return new Date(ts * 1000).toISOString().replace("T", " ").replace("Z", " UTC");
}

/**
 * Hook that triggers re-renders every 30 seconds so relative times stay fresh.
 * Uses requestAnimationFrame to avoid setInterval memory leaks.
 */
export function useRelativeTime(): number {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let rafId: number;
    let lastUpdate = Date.now();

    function update() {
      const now = Date.now();
      if (now - lastUpdate >= 30000) {
        lastUpdate = now;
        setTick((t) => t + 1);
      }
      rafId = requestAnimationFrame(update);
    }

    rafId = requestAnimationFrame(update);
    return () => cancelAnimationFrame(rafId);
  }, []);

  return tick;
}
