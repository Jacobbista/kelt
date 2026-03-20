import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { createPortal, flushSync } from "react-dom";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import { buildLogsWsUrl, getNfLogLevel, setNfLogLevel } from "../api";

const NF_WITH_LOG_LEVEL = ["amf", "smf", "nrf", "udm", "udr", "pcf", "bsf", "nssf", "ausf", "upf-edge", "upf-cloud"];
// Open5GS ogs-log.h hierarchy (each level includes more severe below)
const LOG_LEVELS = ["fatal", "error", "warn", "info", "debug", "trace"];
const LOG_LEVEL_DESC = {
  fatal: "Critical errors causing termination",
  error: "Non-fatal errors",
  warn: "Warning messages",
  info: "General information (default)",
  debug: "Detailed debugging information",
  trace: "Maximum verbosity, including protocol traces",
};

function levelBadgeClasses(level) {
  switch (level) {
    case "fatal": return "bg-rose-800/70 border-rose-600 text-rose-100";
    case "error": return "bg-rose-600/50 border-rose-500 text-rose-100";
    case "warn": case "warning": return "bg-amber-600/50 border-amber-500 text-amber-100";
    case "info": return "bg-emerald-600/50 border-emerald-500 text-emerald-100";
    case "debug": return "bg-slate-600/60 border-slate-500 text-slate-200";
    case "trace": return "bg-indigo-600/50 border-indigo-500 text-indigo-100";
    default: return "bg-slate-700/60 border-slate-600 text-slate-400";
  }
}

function levelTextColor(level) {
  switch (level) {
    case "fatal": return "text-rose-400";
    case "error": return "text-rose-400";
    case "warn": case "warning": return "text-amber-400";
    case "info": return "text-emerald-400";
    case "debug": return "text-slate-400";
    case "trace": return "text-indigo-400";
    default: return "text-slate-400";
  }
}

export default function LogViewer({ namespace, pod, containers, container: initialContainer, deployment, onClose }) {
  const [container, setContainer] = useState(initialContainer || containers?.[0] || "");
  const [search, setSearch] = useState("");
  const [toast, setToast] = useState({ visible: false, show: false });

  const [logLevel, setLogLevel] = useState(null);
  const [logLevelLoading, setLogLevelLoading] = useState(false);
  const [logLevelOpen, setLogLevelOpen] = useState(false);
  const [logLevelError, setLogLevelError] = useState(null);

  const [pendingLevel, setPendingLevel] = useState(null);
  const [applyState, setApplyState] = useState("idle"); // idle | applying
  const [loadFromStart, setLoadFromStart] = useState(false);
  const [atTop, setAtTop] = useState(false); // true when user scrolled to top of logs

  const supportsLogLevel = deployment && NF_WITH_LOG_LEVEL.includes(deployment);

  const termRef = useRef(null);
  const fitRef = useRef(null);
  const searchRef = useRef(null);
  const holderRef = useRef(null);
  const socketRef = useRef(null);

  const useFromStart = loadFromStart;
  const wsUrl = useMemo(
    () => buildLogsWsUrl(namespace, pod, container, useFromStart ? { fromStart: true } : {}),
    [namespace, pod, container, useFromStart]
  );

  useEffect(() => {
    if (!holderRef.current) return;
    const term = new Terminal({
      convertEol: true,
      theme: { background: "#020617", foreground: "#cbd5e1", cursor: "#475569", selectionBackground: "#334155", selectionForeground: "#f1f5f9" },
      fontSize: 12, scrollback: 10000, cursorBlink: false, disableStdin: true,
    });
    const fit = new FitAddon();
    const srch = new SearchAddon();
    term.loadAddon(fit);
    term.loadAddon(srch);
    term.open(holderRef.current);
    term.writeln(`\x1b[90m[dashboard] streaming logs: ${namespace}/${pod}/${container}\x1b[0m`);
    termRef.current = term;
    fitRef.current = fit;
    searchRef.current = srch;

    const socket = new WebSocket(wsUrl);
    socketRef.current = socket;
    let disposed = false;
    const safeWrite = (txt) => { if (!disposed) try { term.writeln(txt); } catch (_) {} };
    const safeFit = () => {
      if (!disposed && holderRef.current?.offsetParent && holderRef.current?.offsetWidth > 0 && holderRef.current?.offsetHeight > 0) {
        try { fit.fit(); } catch (_) {}
      }
    };
    socket.onmessage = (e) => safeWrite(e.data);
    socket.onerror = () => safeWrite("\x1b[31m[dashboard] websocket error\x1b[0m");
    socket.onclose = () => safeWrite("\x1b[90m[dashboard] stream closed\x1b[0m");

    const onResize = () => safeFit();
    window.addEventListener("resize", onResize);

    // Delay initial fit until layout is ready (avoids xterm #4983 dimensions error)
    const rafId = requestAnimationFrame(() => safeFit());

    let scrollCleanup;
    const viewport = holderRef.current?.querySelector(".xterm-viewport");
    if (viewport) {
      const onScroll = () => {
        try {
          if (!viewport || disposed) return;
          const top = viewport.scrollTop <= 2;
          setAtTop((prev) => (prev !== top ? top : prev));
        } catch (_) {}
      };
      viewport.addEventListener("scroll", onScroll);
      onScroll();
      scrollCleanup = () => viewport.removeEventListener("scroll", onScroll);
    }

    return () => {
      disposed = true;
      cancelAnimationFrame(rafId);
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      window.removeEventListener("resize", onResize);
      scrollCleanup?.();
      socket.close();
      // Defer dispose to next frame so FitAddon/viewport timers complete (fixes xterm #4983)
      requestAnimationFrame(() => { try { term.dispose(); } catch (_) {} });
    };
  }, [namespace, pod, container, wsUrl]);

  useEffect(() => {
    const srch = searchRef.current;
    if (!srch || !search) return;
    if (!holderRef.current?.isConnected) return; // skip if terminal container detached
    try {
      srch.findNext(search, { caseSensitive: false, incremental: true });
    } catch (_) {}
  }, [search]);

  useEffect(() => {
    if (!supportsLogLevel) return;
    setLogLevelError(null);
    setLogLevelLoading(true);
    getNfLogLevel(deployment, namespace)
      .then((r) => setLogLevel(r.level))
      .catch((err) => setLogLevelError(String(err?.message || err)))
      .finally(() => setLogLevelLoading(false));
  }, [deployment, namespace, supportsLogLevel]);

  const findNext = useCallback(() => searchRef.current?.findNext(search, { caseSensitive: false }), [search]);
  const findPrev = useCallback(() => searchRef.current?.findPrevious(search, { caseSensitive: false }), [search]);

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

  const copySelection = useCallback(() => { const sel = termRef.current?.getSelection(); if (sel) writeClipboard(sel); }, [writeClipboard]);
  const copyAll = useCallback(() => { const t = termRef.current; if (!t) return; t.selectAll(); const s = t.getSelection(); t.clearSelection(); writeClipboard(s); }, [writeClipboard]);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    const handler = (e) => {
      if (e.type === "keydown" && (e.ctrlKey || e.metaKey) && e.key === "c") {
        const sel = term.getSelection();
        if (sel) { writeClipboard(sel); return false; }
      }
      return true;
    };
    term.attachCustomKeyEventHandler(handler);
  });

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleApplyLogLevel = useCallback(async () => {
    if (!pendingLevel || !deployment) return;
    flushSync(() => setApplyState("applying"));
    try {
      await setNfLogLevel(deployment, pendingLevel, namespace);
      onClose(); // Close immediately; user can reopen Log after NF has restarted
    } catch (err) {
      setLogLevelError(String(err?.message || err));
      setApplyState("idle");
    }
    setPendingLevel(null);
  }, [pendingLevel, deployment, namespace, onClose]);

  const containerList = containers || (container ? [container] : []);
  const isBusy = applyState !== "idle";

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-950/95">
      <div className="m-4 flex flex-1 flex-col overflow-hidden rounded-lg border border-slate-700 bg-slate-900">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-2">
          <div className="mr-auto flex items-center gap-2">
            <span className="text-sm font-medium text-slate-200">Logs</span>
            <span className="font-mono text-xs text-slate-500">{pod}</span>
          </div>

          {containerList.length > 1 && (
            <select value={container} onChange={(e) => setContainer(e.target.value)}
              className="rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-indigo-500">
              {containerList.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          )}

          {/* Search */}
          <div className="flex items-center gap-1 rounded bg-slate-800 border border-slate-700 px-1.5">
            <svg className="h-3.5 w-3.5 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" /></svg>
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.shiftKey ? findPrev() : findNext(); } }}
              placeholder="Search..." className="w-36 bg-transparent py-1 text-xs text-slate-300 placeholder-slate-600 focus:outline-none" />
            <button type="button" onClick={findPrev} className="px-1 text-slate-500 hover:text-slate-300" title="Previous (Shift+Enter)">&#9650;</button>
            <button type="button" onClick={findNext} className="px-1 text-slate-500 hover:text-slate-300" title="Next (Enter)">&#9660;</button>
          </div>

          {/* Log level badge */}
          {supportsLogLevel && (
            <div className="relative">
              <button type="button" onClick={() => setLogLevelOpen((o) => !o)} disabled={logLevelLoading || isBusy}
                className="flex items-center gap-1.5 rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-slate-600 focus:outline-none disabled:opacity-50"
                title="Change log level">
                <span className="text-slate-500">Log level</span>
                <span className={`rounded border px-1.5 py-0.5 font-medium ${levelBadgeClasses(logLevel)}`}>
                  {logLevelLoading ? "…" : logLevel ?? "—"}
                </span>
                {logLevelError && <span className="text-rose-400 text-[10px]" title={logLevelError}>err</span>}
              </button>
              {logLevelOpen && (
                <div className="absolute left-0 top-full mt-1 z-[60] rounded border border-slate-700 bg-slate-900 shadow-xl py-1 min-w-[16rem] max-w-[20rem]">
                  <div className="px-3 py-2 text-[10px] text-slate-500 border-b border-slate-800 space-y-0.5">
                    {LOG_LEVELS.map((l) => (
                      <div key={l}><span className={`font-medium ${levelTextColor(l)}`}>{l}:</span> {LOG_LEVEL_DESC[l]}</div>
                    ))}
                  </div>
                  {LOG_LEVELS.map((l) => (
                    <button key={l} type="button"
                      onClick={() => { setPendingLevel(l); setLogLevelOpen(false); }}
                      className={`block w-full text-left px-3 py-1.5 text-xs hover:bg-slate-800 hover:text-white ${l === logLevel ? "text-white font-medium" : "text-slate-300"}`}>
                      {l}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <button type="button" onClick={copySelection}
            className="rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors"
            title="Copy selected text (Ctrl+C)">Copy Selection</button>
          <button type="button" onClick={copyAll}
            className="rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors">Copy All</button>
          <button type="button" onClick={onClose}
            className="rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:text-rose-400 hover:border-rose-600 transition-colors"
            title="Close (Esc)">✕</button>
        </div>

        <div className="relative flex-1 min-h-0 p-1">
          <div ref={holderRef} className="h-full w-full" />
          {atTop && !loadFromStart && (
            <button
              type="button"
              onClick={() => setLoadFromStart(true)}
              className="absolute top-2 left-1/2 z-10 -translate-x-1/2 rounded bg-slate-800/95 px-3 py-1.5 text-xs text-slate-300 shadow-lg ring-1 ring-slate-600 hover:bg-slate-700 hover:text-white transition-colors"
              title="Load full log from pod start"
            >
              Load earlier logs
            </button>
          )}
        </div>
      </div>

      {toast.visible && (
        <div className={`absolute left-1/2 top-4 -translate-x-1/2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-lg transition-all duration-300 ${toast.show ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-2"}`}>
          Copied!
        </div>
      )}

      {/* Confirmation modal + loading overlay — portalled to body to avoid z-index/overflow issues */}
      {pendingLevel != null && createPortal(
        <div className="fixed inset-0 z-[9998] flex items-center justify-center" style={{ backgroundColor: "rgba(2, 6, 23, 0.85)" }}
          onClick={() => !isBusy && setPendingLevel(null)}>
          <div className="rounded-lg border border-slate-700 bg-slate-900 px-5 py-4 shadow-2xl max-w-sm w-full mx-4"
            onClick={(e) => e.stopPropagation()}>
            <p className="text-sm text-slate-200 mb-4">
              The ConfigMap will be updated and the deployment <span className="font-mono text-indigo-300">{deployment}</span> will
              be restarted with level <span className="font-medium text-white">{pendingLevel}</span>. Proceed?
            </p>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setPendingLevel(null)} disabled={isBusy}
                className="rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600 disabled:opacity-40">Cancel</button>
              <button type="button" onClick={handleApplyLogLevel} disabled={isBusy}
                className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-60">Apply</button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {isBusy && createPortal(
        <div className="fixed inset-0 z-[9999] flex flex-col items-center justify-center" style={{ backgroundColor: "rgba(2, 6, 23, 0.95)" }}>
          <span className="mb-4 h-12 w-12 animate-spin rounded-full border-4 border-indigo-500 border-t-transparent" />
          <p className="text-base font-semibold text-slate-200">Applying log level…</p>
          <p className="mt-2 text-sm text-slate-500">
            Updating ConfigMap and restarting <span className="font-mono text-slate-400">{deployment}</span>.
          </p>
        </div>,
        document.body
      )}
    </div>
  );
}
