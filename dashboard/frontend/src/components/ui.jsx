// Small shared UI building blocks for the Services-area pages, in the house
// (NfCard) idiom: bordered panels, status banner, colored action buttons, a
// copy block. Kept intentionally plain (ops dashboard, not a showcase).
import { useEffect, useState } from "react";
import { IconCopy } from "./icons";

export const inputCls =
  "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-sky-600 focus:outline-none";

export const btn = {
  sky: "rounded bg-sky-600/20 px-3 py-1.5 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-600/30 disabled:opacity-40",
  amber: "rounded bg-amber-600/20 px-3 py-1.5 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-600/30 disabled:opacity-40",
  indigo: "rounded bg-indigo-600/20 px-3 py-1.5 text-xs font-medium text-indigo-300 transition-colors hover:bg-indigo-600/30 disabled:opacity-40",
  ghost: "rounded bg-slate-700/60 px-2 py-1 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-700",
};

export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="flex gap-1 border-b border-slate-800">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-xs font-medium transition-colors ${
            active === t.id ? "border-sky-500 text-sky-300" : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Panel({ title, hint, right, children }) {
  return (
    <section className="rounded-lg border border-slate-700 bg-slate-900">
      <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-4 py-2.5">
        <div>
          <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
          {hint && <p className="text-[11px] text-slate-500">{hint}</p>}
        </div>
        {right}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

// Centered modal dialog: backdrop, Esc / X / click-outside to close, scrollable
// body, body-scroll lock. Use for focused actions (configure, deploy) instead of
// expanding panels inline.
export function Modal({ title, hint, onClose, children, wide }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className={`flex max-h-[85vh] w-full ${wide ? "max-w-3xl" : "max-w-xl"} flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-2xl`}>
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 px-5 py-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
            {hint && <p className="mt-0.5 text-[11px] text-slate-500">{hint}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">✕</button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function Banner({ msg }) {
  if (!msg) return null;
  const ok = msg.kind === "ok";
  return (
    <div className={`rounded border px-3 py-2 text-xs ${ok ? "border-emerald-700/40 bg-emerald-950/30 text-emerald-300" : "border-rose-700/40 bg-rose-950/30 text-rose-300"}`}>
      {msg.text}
    </div>
  );
}

export function CopyBlock({ text, label }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* selectable below */ }
  };
  return (
    <div className="rounded border border-slate-800 bg-slate-950 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
        <button type="button" onClick={copy} className="inline-flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200">
          <IconCopy size={13} /> {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all text-[11px] font-mono text-slate-300">{text}</pre>
    </div>
  );
}
