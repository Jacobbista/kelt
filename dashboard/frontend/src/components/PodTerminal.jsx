import React, { useEffect, useRef, useState, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { buildExecWsUrl } from "../api";

const SHORTCUTS_COMMON = [
  { label: "ip addr", cmd: "ip addr\n" },
  { label: "ip route", cmd: "ip route\n" },
  { label: "ps aux", cmd: "ps aux\n" },
  { label: "resolv.conf", cmd: "cat /etc/resolv.conf\n" },
];

const SHORTCUTS_MONGO = [
  { label: "Connect DB", cmd: "mongo open5gs\n" },
  { label: "Collections", cmd: "db.getCollectionNames()\n" },
  { label: "Count Subs", cmd: "db.subscribers.count()\n" },
  { label: "List Subs", cmd: "db.subscribers.find().pretty()\n" },
];

export default function PodTerminal({
  namespace,
  pod,
  containers,
  container: initialContainer,
  nfType,
  command: customCommand,
  title: customTitle,
  onClose,
}) {
  const [container, setContainer] = useState(
    initialContainer || containers?.[0] || "",
  );
  const [toast, setToast] = useState({ visible: false, show: false });
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const socketRef = useRef(null);
  const holderRef = useRef(null);

  const isMongo = nfType === "mongodb" || pod?.includes("mongo");
  const shortcuts = isMongo
    ? [...SHORTCUTS_MONGO, ...SHORTCUTS_COMMON]
    : SHORTCUTS_COMMON;

  const showToast = useCallback(() => {
    setToast({ visible: true, show: false });
    requestAnimationFrame(() => requestAnimationFrame(() => setToast({ visible: true, show: true })));
    setTimeout(() => setToast((t) => ({ ...t, show: false })), 1500);
    setTimeout(() => setToast({ visible: false, show: false }), 1900);
  }, []);

  const writeClipboard = useCallback((text) => {
    if (!text) return;
    const done = () => showToast();
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => {});
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      done();
    }
  }, [showToast]);

  const copySelection = useCallback(() => {
    const sel = termRef.current?.getSelection();
    if (sel) writeClipboard(sel);
  }, [writeClipboard]);

  const sendInput = useCallback((data) => {
    const ws = socketRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stdin", data }));
    }
  }, []);

  useEffect(() => {
    if (!holderRef.current) return;

    const term = new Terminal({
      convertEol: false,
      theme: {
        background: "#020617",
        foreground: "#cbd5e1",
        cursor: "#6366f1",
        selectionBackground: "#334155",
        selectionForeground: "#f1f5f9",
      },
      fontSize: 13,
      scrollback: 5000,
      cursorBlink: true,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(holderRef.current);
    termRef.current = term;
    fitRef.current = fit;

    let disposed = false;
    const safeWrite = (txt) => { if (!disposed) try { term.write(txt); } catch (_) {} };
    const safeFit = () => {
      if (!disposed && holderRef.current?.offsetParent && holderRef.current?.offsetWidth > 0 && holderRef.current?.offsetHeight > 0) {
        try { fit.fit(); } catch (_) {}
      }
    };
    requestAnimationFrame(() => safeFit());

    const wsUrl = buildExecWsUrl(namespace, pod, container, customCommand || "/bin/bash");
    const socket = new WebSocket(wsUrl);
    socketRef.current = socket;

    socket.onopen = () => {
      const dims = { type: "resize", cols: term.cols, rows: term.rows };
      socket.send(JSON.stringify(dims));
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "stdout" && msg.data) safeWrite(msg.data);
      } catch {
        safeWrite(event.data);
      }
    };

    socket.onerror = () => safeWrite("\r\n\x1b[31m[connection error]\x1b[0m\r\n");
    socket.onclose = () => safeWrite("\r\n\x1b[90m[session ended]\x1b[0m\r\n");

    // Forward terminal input to WebSocket
    term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "stdin", data }));
      }
    });

    // Ctrl+C: copy selection if text selected, otherwise send to shell
    term.attachCustomKeyEventHandler((e) => {
      if (e.type === "keydown" && (e.ctrlKey || e.metaKey) && e.key === "c") {
        const sel = term.getSelection();
        if (sel) {
          writeClipboard(sel);
          return false;
        }
      }
      return true;
    });

    // Forward resize events
    term.onResize(({ cols, rows }) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    });

    const onResize = () => safeFit();
    window.addEventListener("resize", onResize);

    return () => {
      disposed = true;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      window.removeEventListener("resize", onResize);
      socket.close();
      requestAnimationFrame(() => { try { term.dispose(); } catch (_) {} });
    };
  }, [namespace, pod, container, customCommand, writeClipboard]);

  // Close on Escape
  useEffect(() => {
    const handler = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const containerList = containers || (container ? [container] : []);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-950/95">
      <div className="m-4 flex flex-1 flex-col overflow-hidden rounded-lg border border-slate-700 bg-slate-900">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-2">
          <div className="mr-auto flex items-center gap-2">
            <span className={`text-sm font-medium ${customCommand ? "text-cyan-400" : "text-emerald-400"}`}>
              {customTitle || "Terminal"}
            </span>
            <span className="font-mono text-xs text-slate-500">{pod}</span>
          </div>

          {containerList.length > 1 && (
            <select
              value={container}
              onChange={(e) => setContainer(e.target.value)}
              className="rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-indigo-500"
            >
              {containerList.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          )}

          <button
            type="button"
            onClick={copySelection}
            className="rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors"
            title="Copy selected text (Ctrl+C)"
          >
            Copy Selection
          </button>

          <button
            type="button"
            onClick={onClose}
            className="rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:text-rose-400 hover:border-rose-600 transition-colors"
            title="Close (Esc)"
          >
            ✕
          </button>
        </div>

        {/* Shortcuts (hidden for custom command like tail -f) */}
        {!customCommand && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-800/50 px-3 py-1.5">
          <span className="text-[10px] text-slate-600 mr-1">Quick:</span>
          {shortcuts.map((s) => (
            <button
              key={s.label}
              type="button"
              onClick={() => sendInput(s.cmd)}
              className="rounded bg-slate-800/80 border border-slate-700/50 px-2 py-0.5 text-[10px] font-mono text-slate-400 hover:text-indigo-300 hover:border-indigo-600/50 transition-colors"
            >
              {s.label}
            </button>
          ))}
        </div>
        )}

        {/* Terminal */}
        <div ref={holderRef} className="flex-1 p-1" />
      </div>

      {toast.visible && (
        <div
          className={`absolute left-1/2 top-4 -translate-x-1/2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-lg transition-all duration-300 ${
            toast.show ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-2"
          }`}
        >
          Copied!
        </div>
      )}
    </div>
  );
}
