import { useEffect, useRef, useState, useCallback } from "react";
import { getCurrentAccessToken } from "../auth/AuthContext";

function buildWsUrl() {
  const url = new URL("/api/v1/ws/traffic/intensity", window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const token = getCurrentAccessToken();
  if (token) url.searchParams.set("access_token", token);
  return url.toString();
}

export default function useTrafficStream() {
  const [links, setLinks] = useState({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const retryRef = useRef(null);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    try {
      const ws = new WebSocket(buildWsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        if (mountedRef.current) setConnected(true);
      };

      ws.onmessage = (evt) => {
        if (!mountedRef.current) return;
        try {
          const data = JSON.parse(evt.data);
          if (data.links) setLinks(data.links);
        } catch { /* ignore parse errors */ }
      };

      ws.onclose = () => {
        if (mountedRef.current) {
          setConnected(false);
          retryRef.current = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      if (mountedRef.current) {
        retryRef.current = setTimeout(connect, 3000);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(retryRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [connect]);

  return { links, connected };
}
