import React, { useEffect, useMemo, useRef } from "react";
import { Terminal } from "xterm";
import { buildLogsWsUrl } from "../api";

export default function LogViewer({ namespace, pod, container, onClose }) {
  const termRef = useRef(null);
  const holderRef = useRef(null);
  const socketRef = useRef(null);
  const wsUrl = useMemo(() => buildLogsWsUrl(namespace, pod, container), [namespace, pod, container]);

  useEffect(() => {
    if (!holderRef.current) return;
    const term = new Terminal({
      convertEol: true,
      theme: { background: "#020617", foreground: "#cbd5e1" },
      fontSize: 12
    });
    term.open(holderRef.current);
    term.writeln(`[dashboard] streaming logs for ${namespace}/${pod}`);
    termRef.current = term;

    const socket = new WebSocket(wsUrl);
    socketRef.current = socket;
    socket.onmessage = (event) => term.writeln(event.data);
    socket.onerror = () => term.writeln("[dashboard] websocket error");
    socket.onclose = () => term.writeln("[dashboard] stream closed");

    return () => {
      socket.close();
      term.dispose();
    };
  }, [namespace, pod, wsUrl]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80">
      <div className="w-[90vw] max-w-5xl rounded border border-slate-700 bg-slate-900 p-3">
        <div className="mb-3 flex items-center justify-between">
          <div className="font-medium">
            Log Stream - {namespace}/{pod}
          </div>
          <button type="button" className="rounded bg-slate-700 px-2 py-1 text-sm" onClick={onClose}>
            Close
          </button>
        </div>
        <div ref={holderRef} className="h-[60vh] overflow-hidden rounded border border-slate-700" />
      </div>
    </div>
  );
}
