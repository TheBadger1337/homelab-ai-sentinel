import { useEffect, useRef, useCallback } from "react";
import type { SSEEvent } from "../lib/types";

interface UseSSEOptions {
  onEvent: (event: SSEEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

/**
 * Hook to subscribe to Sentinel's SSE event stream.
 * Reconnects automatically on disconnect with exponential backoff.
 */
export function useSSE(onEventOrOptions: ((event: SSEEvent) => void) | UseSSEOptions) {
  const opts: UseSSEOptions =
    typeof onEventOrOptions === "function"
      ? { onEvent: onEventOrOptions }
      : onEventOrOptions;

  const optsRef = useRef(opts);
  optsRef.current = opts;

  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectDelay = 1000;
    let mounted = true;

    function connect() {
      if (!mounted) return;
      es = new EventSource("/api/events");

      es.onopen = () => {
        optsRef.current.onOpen?.();
        reconnectDelay = 1000;
      };

      es.onmessage = (e) => {
        try {
          const parsed = JSON.parse(e.data) as SSEEvent;
          optsRef.current.onEvent(parsed);
        } catch {
          // Ignore malformed events
        }
      };

      es.onerror = () => {
        es?.close();
        optsRef.current.onClose?.();
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
