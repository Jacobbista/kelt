/**
 * Reusable loader with 5G testbed branding.
 * 5 signal bars animate in sequence (5G-style) with optional timer.
 *
 * Usage:
 *   import Loader from "./Loader";
 *
 *   // Minimal
 *   <Loader />
 *
 *   // With label and elapsed timer (e.g. during Enable/Disable)
 *   <Loader size="sm" label="Applying…" elapsed={elapsed} />
 *
 *   // Sizes: sm | md | lg
 *   <Loader size="lg" label="Loading RAN state…" />
 *
 * Props: size, label, elapsed (seconds), className
 */
import React from "react";

const BAR_COUNT = 5;

export default function Loader({ size = "md", label, elapsed, className = "" }) {
  const sizes = {
    sm: "h-6 gap-0.5",
    md: "h-10 gap-1",
    lg: "h-14 gap-1.5",
  };
  const barHeights = {
    sm: ["h-1", "h-2", "h-3", "h-4", "h-5"],
    md: ["h-1.5", "h-3", "h-4", "h-5", "h-6"],
    lg: ["h-2", "h-4", "h-5", "h-6", "h-8"],
  };
  const s = size in sizes ? size : "md";

  return (
    <div className={`flex flex-col items-center gap-3 ${className}`}>
      <div className={`flex items-end ${sizes[s]}`} aria-hidden="true">
        {Array.from({ length: BAR_COUNT }).map((_, i) => (
          <div
            key={i}
            className={`w-1.5 rounded-sm bg-indigo-500/80 ${barHeights[s][i]} animate-loader-bar`}
            style={{ animationDelay: `${i * 0.12}s` }}
          />
        ))}
      </div>
      {(label || elapsed != null) && (
        <div className="flex flex-col items-center gap-0.5 text-xs text-slate-400">
          {label && <span>{label}</span>}
          {elapsed != null && (
            <span className="font-mono tabular-nums text-slate-500">
              {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
