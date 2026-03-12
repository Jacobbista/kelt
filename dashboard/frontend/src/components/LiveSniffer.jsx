import React, { useCallback, useEffect, useRef, useState } from "react";
import { buildSnifferWsUrl, getSnifferPoints, runPathTrace } from "../api";

const HOP_ORDER = ["br-n3", "upf-n3", "upf-ogstun", "upf-n6"];

const HOP_COLORS = {
  active: {
    bg: "bg-emerald-900/40",
    border: "border-emerald-600",
    dot: "bg-emerald-400",
    text: "text-emerald-400",
  },
  silent: {
    bg: "bg-amber-900/30",
    border: "border-amber-600/50",
    dot: "bg-amber-400",
    text: "text-amber-400",
  },
  error: {
    bg: "bg-rose-900/30",
    border: "border-rose-600/50",
    dot: "bg-rose-400",
    text: "text-rose-400",
  },
  pending: {
    bg: "bg-slate-900",
    border: "border-slate-700",
    dot: "bg-slate-500 animate-pulse",
    text: "text-slate-400",
  },
};

function PathTraceDiagram({ traceResults }) {
  if (!traceResults || traceResults.length === 0) return null;

  const hops = HOP_ORDER.map((id) =>
    traceResults.find((r) => r.point_id === id),
  ).filter(Boolean);
  const nodeW = 130;
  const nodeH = 56;
  const gap = 40;
  const arrowSize = 8;
  const svgW = hops.length * (nodeW + gap) - gap + 40;
  const svgH = 100;

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${svgW} ${svgH}`}
        className="w-full"
        style={{ minWidth: 500, maxHeight: 100 }}
      >
        {hops.map((hop, i) => {
          const x = 20 + i * (nodeW + gap);
          const y = (svgH - nodeH) / 2;
          const isActive = hop.status === "active";
          const isSilent = hop.status === "silent";
          const isError = hop.status === "error";

          const fill = isActive
            ? "#064e3b"
            : isSilent
              ? "#451a03"
              : isError
                ? "#4c0519"
                : "#0f172a";
          const stroke = isActive
            ? "#10b981"
            : isSilent
              ? "#d97706"
              : isError
                ? "#e11d48"
                : "#475569";
          const textFill = isActive
            ? "#6ee7b7"
            : isSilent
              ? "#fbbf24"
              : isError
                ? "#fda4af"
                : "#94a3b8";

          return (
            <g key={hop.point_id}>
              {i > 0 &&
                (() => {
                  const prevX = 20 + (i - 1) * (nodeW + gap) + nodeW;
                  const arrY = svgH / 2;
                  const prevHop = hops[i - 1];
                  const linkActive = prevHop?.status === "active" && isActive;
                  const linkStroke = linkActive ? "#34d399" : "#334155";
                  return (
                    <g>
                      <line
                        x1={prevX}
                        y1={arrY}
                        x2={x - arrowSize}
                        y2={arrY}
                        stroke={linkStroke}
                        strokeWidth={linkActive ? 2.5 : 1.5}
                        strokeDasharray={linkActive ? undefined : "4 3"}
                      />
                      <polygon
                        points={`${x},${arrY} ${x - arrowSize},${arrY - arrowSize / 2} ${x - arrowSize},${arrY + arrowSize / 2}`}
                        fill={linkStroke}
                      />
                      {linkActive && (
                        <circle r="3" fill="#34d399">
                          <animateMotion
                            dur="1.5s"
                            repeatCount="indefinite"
                            path={`M ${prevX} ${arrY} L ${x - arrowSize} ${arrY}`}
                          />
                        </circle>
                      )}
                    </g>
                  );
                })()}

              <rect
                x={x}
                y={y}
                width={nodeW}
                height={nodeH}
                rx={8}
                fill={fill}
                stroke={stroke}
                strokeWidth={1.5}
              />

              <circle cx={x + 12} cy={y + 14} r={4} fill={stroke}>
                {isActive && (
                  <animate
                    attributeName="opacity"
                    values="1;0.4;1"
                    dur="1.5s"
                    repeatCount="indefinite"
                  />
                )}
              </circle>

              <text
                x={x + 22}
                y={y + 18}
                fill={textFill}
                fontSize="10"
                fontWeight="600"
              >
                {hop.label.replace(" (Worker)", "").replace("UPF ", "")}
              </text>
              <text
                x={x + nodeW / 2}
                y={y + 36}
                textAnchor="middle"
                fill={textFill}
                fontSize="10"
                fontWeight="700"
              >
                {hop.packets > 0
                  ? `${hop.packets} pkts`
                  : hop.status === "error"
                    ? "ERR"
                    : "0 pkts"}
              </text>
              <text
                x={x + nodeW / 2}
                y={y + 50}
                textAnchor="middle"
                fill="#64748b"
                fontSize="8"
              >
                {hop.protocol}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function TerminalOutput({ lines, captureActive, noTrafficYet }) {
  const containerRef = useRef(null);
  const shouldAutoScroll = useRef(true);
  const [copyStatus, setCopyStatus] = useState("idle"); // idle | copied | failed

  useEffect(() => {
    if (shouldAutoScroll.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines]);

  function handleScroll() {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    shouldAutoScroll.current = scrollHeight - scrollTop - clientHeight < 40;
  }

  const fullText = [
    ...lines,
    captureActive && noTrafficYet ? "Waiting for traffic..." : null,
    captureActive ? "● Capturing..." : null,
  ]
    .filter(Boolean)
    .join("\n");

  function copyViaTextarea(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.left = "-1000px";
    document.body.appendChild(ta);
    ta.select();
    try {
      return document.execCommand("copy");
    } finally {
      document.body.removeChild(ta);
    }
  }

  async function handleCopy() {
    try {
      // navigator.clipboard requires a secure context (https/localhost) in many browsers
      if (navigator.clipboard?.writeText && window.isSecureContext) {
        await navigator.clipboard.writeText(fullText);
      } else {
        const ok = copyViaTextarea(fullText);
        if (!ok) throw new Error("copy_failed");
      }
      setCopyStatus("copied");
      setTimeout(() => setCopyStatus("idle"), 1500);
    } catch (_) {
      setCopyStatus("failed");
      setTimeout(() => setCopyStatus("idle"), 2000);
    }
  }

  return (
    <div className="relative">
      <pre
        ref={containerRef}
        onScroll={handleScroll}
        className="relative z-0 h-64 overflow-y-auto overflow-x-auto rounded bg-black/80 border border-slate-700 font-mono text-[11px] leading-relaxed p-3 pr-16 select-text whitespace-pre-wrap break-all"
      >
        {lines.length === 0 && !captureActive && (
          <span className="text-slate-600">
            Select a capture point and click Start to begin sniffing...
          </span>
        )}
        {lines.map((line, i) => {
          let cls = "text-slate-300";
          if (line.startsWith("[sniffer]")) cls = "text-cyan-400";
          else if (line.startsWith("[error]")) cls = "text-rose-400";
          else if (line === "---") cls = "text-slate-600";
          return (
            <span key={i} className={cls}>
              {line}
              {"\n"}
            </span>
          );
        })}
        {captureActive && noTrafficYet && (
          <span className="text-amber-400/70">
            Waiting for traffic...{"\n"}
          </span>
        )}
        {captureActive && (
          <span className="text-emerald-500 animate-pulse">
            ● Capturing...{"\n"}
          </span>
        )}
      </pre>
      <div className="absolute top-2 right-8 z-20 pointer-events-auto">
        <button
          type="button"
          onClick={handleCopy}
          disabled={lines.length === 0}
          className="rounded bg-slate-700/90 px-2 py-1 text-[10px] font-medium text-slate-300 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {copyStatus === "copied"
            ? "Copied!"
            : copyStatus === "failed"
              ? "Copy failed"
              : "Copy"}
        </button>
      </div>
    </div>
  );
}

export default function LiveSniffer() {
  const [points, setPoints] = useState([]);
  const [selectedPoint, setSelectedPoint] = useState("br-n3");
  const [customFilter, setCustomFilter] = useState("");
  const [captureActive, setCaptureActive] = useState(false);
  const [lines, setLines] = useState([]);
  const [packetCount, setPacketCount] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const wsRef = useRef(null);
  const timerRef = useRef(null);

  const [traceResults, setTraceResults] = useState(null);
  const [tracing, setTracing] = useState(false);
  const [traceError, setTraceError] = useState(null);
  const [traceElapsed, setTraceElapsed] = useState(0);
  const [traceDuration, setTraceDuration] = useState(5);
  const traceTimerRef = useRef(null);

  useEffect(() => {
    getSnifferPoints()
      .then(setPoints)
      .catch(() => {});
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startCapture = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
    }
    setLines([]);
    setPacketCount(0);
    setElapsed(0);
    setCaptureActive(true);

    const url = buildSnifferWsUrl(selectedPoint, {
      filter: customFilter || undefined,
    });

    const ws = new WebSocket(url);
    wsRef.current = ws;

    const startTime = Date.now();
    timerRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    }, 1000);

    ws.onopen = () => {
      setLines((prev) => [...prev, "[sniffer] Connected"]);
    };

    ws.onmessage = (evt) => {
      const text = evt.data;
      setLines((prev) => [...prev, text]);
      if (!text.startsWith("[") && text !== "---") {
        setPacketCount((c) => c + 1);
      }
    };

    ws.onclose = () => {
      setCaptureActive(false);
      clearTimer();
      wsRef.current = null;
    };

    ws.onerror = () => {
      setCaptureActive(false);
      clearTimer();
      setLines((prev) => [...prev, "[error] WebSocket connection failed"]);
    };
  }, [selectedPoint, customFilter, clearTimer]);

  const stopCapture = useCallback(() => {
    if (wsRef.current) {
      try {
        wsRef.current.send("stop");
      } catch (_) {}
      wsRef.current.close();
      wsRef.current = null;
    }
    setCaptureActive(false);
    clearTimer();
  }, [clearTimer]);

  useEffect(() => {
    return () => {
      clearTimer();
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [clearTimer]);

  const hasTraffic = packetCount > 0;
  const noTrafficYet = captureActive && !hasTraffic && elapsed >= 5;

  async function handlePathTrace() {
    setTracing(true);
    setTraceError(null);
    setTraceResults(null);
    setTraceElapsed(0);

    const startTime = Date.now();
    traceTimerRef.current = setInterval(() => {
      setTraceElapsed(Math.floor((Date.now() - startTime) / 1000));
    }, 200);

    try {
      const data = await runPathTrace(traceDuration);
      setTraceResults(data);
    } catch (err) {
      setTraceError(err.message);
    } finally {
      setTracing(false);
      if (traceTimerRef.current) {
        clearInterval(traceTimerRef.current);
        traceTimerRef.current = null;
      }
    }
  }

  useEffect(() => {
    return () => {
      if (traceTimerRef.current) clearInterval(traceTimerRef.current);
    };
  }, []);

  function formatTime(s) {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  }

  const currentPoint = points.find((p) => p.id === selectedPoint);

  return (
    <div className="space-y-4">
      {/* Path Trace */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-medium text-slate-300">
              Data Path Trace
            </h3>
            <p className="text-[10px] text-slate-500 mt-0.5">
              Simultaneous capture at each hop to find where traffic stops
            </p>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[10px] text-slate-500">Duration:</label>
            <select
              value={traceDuration}
              onChange={(e) => setTraceDuration(Number(e.target.value))}
              disabled={tracing}
              className="rounded bg-slate-950 border border-slate-700 px-1.5 py-1 text-xs text-slate-300 focus:border-indigo-500 focus:outline-none"
            >
              <option value={3}>3s</option>
              <option value={5}>5s</option>
              <option value={10}>10s</option>
              <option value={15}>15s</option>
            </select>
            <button
              onClick={handlePathTrace}
              disabled={tracing}
              className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {tracing ? "Tracing..." : "Run Path Trace"}
            </button>
          </div>
        </div>

        {tracing && (
          <div className="flex items-center justify-center gap-2 py-4">
            <div className="h-3 w-3 rounded-full border-2 border-indigo-400 border-t-transparent animate-spin" />
            <span className="text-xs text-slate-400">
              Capturing at all hops... {traceElapsed}s / {traceDuration}s
            </span>
            <div className="w-24 h-1.5 rounded-full bg-slate-700 overflow-hidden">
              <div
                className="h-full bg-indigo-500 rounded-full transition-all duration-200"
                style={{
                  width: `${Math.min(100, (traceElapsed / (traceDuration + 2)) * 100)}%`,
                }}
              />
            </div>
          </div>
        )}

        {traceError && (
          <div className="rounded border border-rose-700/40 bg-rose-950/30 p-2 text-xs text-rose-300">
            {traceError}
          </div>
        )}

        {traceResults && (
          <div className="space-y-3">
            <PathTraceDiagram traceResults={traceResults} />
            <div className="space-y-1">
              {traceResults.map((hop) => {
                const st = HOP_COLORS[hop.status] || HOP_COLORS.pending;
                return (
                  <div
                    key={hop.point_id}
                    className={`flex items-center gap-3 rounded px-3 py-2 ${st.bg} text-xs`}
                  >
                    <span className={`h-2 w-2 rounded-full ${st.dot}`} />
                    <span className="w-32 font-semibold text-slate-200">
                      {hop.label}
                    </span>
                    <span className={`w-16 font-mono ${st.text}`}>
                      {hop.packets > 0 ? `${hop.packets} pkts` : hop.status}
                    </span>
                    <span className="flex-1 text-slate-500 truncate">
                      {hop.sample_lines?.[0] || hop.error || hop.description}
                    </span>
                  </div>
                );
              })}
            </div>

            {(() => {
              const firstSilent = traceResults.find(
                (r) => r.status === "silent",
              );
              const lastActive = [...traceResults]
                .reverse()
                .find((r) => r.status === "active");
              if (firstSilent && lastActive) {
                return (
                  <div className="rounded border border-amber-700/40 bg-amber-950/20 p-2 text-xs text-amber-300">
                    Traffic stops between <strong>{lastActive.label}</strong>{" "}
                    and <strong>{firstSilent.label}</strong>. Check the
                    configuration at this boundary.
                  </div>
                );
              }
              if (traceResults.every((r) => r.status === "silent")) {
                return (
                  <div className="rounded border border-rose-700/40 bg-rose-950/20 p-2 text-xs text-rose-300">
                    No traffic detected at any hop. Ensure a PDU session is
                    active and the UE is sending data.
                  </div>
                );
              }
              if (traceResults.every((r) => r.status === "active")) {
                return (
                  <div className="rounded border border-emerald-700/40 bg-emerald-950/20 p-2 text-xs text-emerald-300">
                    Traffic flows through all hops. The data path is
                    operational.
                  </div>
                );
              }
              return null;
            })()}
          </div>
        )}

        {!traceResults && !tracing && (
          <div className="py-4">
            <PathTraceDiagram
              traceResults={HOP_ORDER.map((id) => {
                const p = points.find((pt) => pt.id === id) || {};
                return {
                  point_id: id,
                  label: p.label || id,
                  description: p.description || "",
                  protocol: p.protocol || "",
                  status: "pending",
                  packets: 0,
                  sample_lines: [],
                };
              })}
            />
            <p className="text-[10px] text-slate-600 text-center mt-2">
              Click "Run Path Trace" to test all data-path hops simultaneously
            </p>
          </div>
        )}
      </div>

      {/* Live Capture */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-slate-300">
            Live Packet Capture
          </h3>
          {captureActive && (
            <div className="flex items-center gap-3 text-xs">
              <span className="text-slate-500">{formatTime(elapsed)}</span>
              <span className="text-emerald-400 font-mono">
                {packetCount} pkts
              </span>
            </div>
          )}
        </div>

        <div className="flex items-end gap-3 mb-3">
          <div className="flex-1">
            <label className="block text-[10px] text-slate-500 uppercase mb-1">
              Capture Point
            </label>
            <select
              value={selectedPoint}
              onChange={(e) => setSelectedPoint(e.target.value)}
              disabled={captureActive}
              className="w-full rounded bg-slate-950 border border-slate-700 px-2 py-1.5 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
            >
              {points.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label} — {p.interface}
                </option>
              ))}
            </select>
          </div>

          <div className="w-48">
            <label className="block text-[10px] text-slate-500 uppercase mb-1">
              BPF Filter (optional)
            </label>
            <input
              type="text"
              value={customFilter}
              onChange={(e) => setCustomFilter(e.target.value)}
              disabled={captureActive}
              placeholder={currentPoint?.default_filter || "e.g. udp port 2152"}
              className="w-full rounded bg-slate-950 border border-slate-700 px-2 py-1.5 text-xs text-slate-200 font-mono placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none"
            />
          </div>

          {!captureActive ? (
            <button
              onClick={startCapture}
              className="rounded bg-emerald-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-emerald-500"
            >
              Start
            </button>
          ) : (
            <button
              onClick={stopCapture}
              className="rounded bg-rose-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-rose-500"
            >
              Stop
            </button>
          )}
        </div>

        {currentPoint && (
          <div className="text-[10px] text-slate-500 mb-2">
            {currentPoint.description} — Protocol: {currentPoint.protocol}
          </div>
        )}

        <TerminalOutput
          lines={lines}
          captureActive={captureActive}
          noTrafficYet={noTrafficYet}
        />
      </div>
    </div>
  );
}
