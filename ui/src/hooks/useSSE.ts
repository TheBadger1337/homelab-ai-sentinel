import { useEffect, useRef, useCallback } from "react";
import type { SSEEvent } from "../lib/types";

/**
 * Hook to subscribe to Sentinel's SSE event stream.
 * Reconnects automatically on disconnect with exponential backoff.
 * Calls onEvent for each received event.
 */
export function useSSE(onEvent: (event: SSEEvent) => void) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectDelay = 1000;
    let mounted = true;

    function connect() {
      if (!mounted) return;
      es = new EventSource("/api/events");

      es.onmessage = (e) => {
        try {
          const parsed = JSON.parse(e.data) as SSEEvent;
          onEventRef.current(parsed);
          reconnectDelay = 1000; // reset on success
        } catch {
          // Ignore malformed events
        }
      };

      es.onerror = () => {
        es?.close();
        if (mounted) {
          setTimeout(connect, reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        }
      };
    }

    connect();

    return () => {
      mounted = false;
      es?.close();
    };
  }, []);
}

/**
 * Hook to subscribe to SSE with a simple callback pattern.
 * Returns a stable disconnect function.
 */
export function useSSECallback() {
  const listenersRef = useRef<Set<(event: SSEEvent) => void>>(new Set());

  const subscribe = useCallback((listener: (event: SSEEvent) => void) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  useSSE((event) => {
    listenersRef.current.forEach((fn) => fn(event));
  });

  return { subscribe };
}
