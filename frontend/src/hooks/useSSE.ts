import { useEffect, useRef, useCallback } from 'react';

type EventHandler = (event: string, data: any) => void;

export function useSSE(url: string | null, onEvent: EventHandler) {
  const readerRef = useRef<ReadableStreamDefaultReader | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const connect = useCallback(() => {
    if (!url) return;

    const controller = new AbortController();
    abortRef.current = controller;

    fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok || !response.body) return;
        const reader = response.body.getReader();
        readerRef.current = reader;
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          let eventType = '';
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                onEvent(eventType || 'message', data);
              } catch { /* ignore parse errors */ }
              eventType = '';
            }
          }
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          console.error('SSE error:', err);
        }
      });
  }, [url, onEvent]);

  useEffect(() => {
    connect();
    return () => {
      abortRef.current?.abort();
      readerRef.current?.cancel();
    };
  }, [connect]);

  const disconnect = useCallback(() => {
    abortRef.current?.abort();
    readerRef.current?.cancel();
  }, []);

  return { disconnect };
}
