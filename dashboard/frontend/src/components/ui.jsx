// Small shared UI building blocks for the Services-area pages, in the house
// (NfCard) idiom: bordered panels, status banner, colored action buttons, a
// copy block. Kept intentionally plain (ops dashboard, not a showcase).
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { IconCopy } from "./icons";

export const inputCls =
  "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-sky-600 focus:outline-none";

export const btn = {
  sky: "rounded bg-sky-600/20 px-3 py-1.5 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-600/30 disabled:opacity-40",
  amber: "rounded bg-amber-600/20 px-3 py-1.5 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-600/30 disabled:opacity-40",
  indigo: "rounded bg-indigo-600/20 px-3 py-1.5 text-xs font-medium text-indigo-300 transition-colors hover:bg-indigo-600/30 disabled:opacity-40",
  ghost: "rounded bg-slate-700/60 px-2 py-1 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-700",
};

// Labeled form field: small uppercase label above the control, optional hint below.
export function Field({ label, hint, children, className = "" }) {
  return (
    <label className={`flex flex-col gap-1 ${className}`}>
      <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">{label}</span>
      {children}
      {hint && <span className="text-[10px] text-slate-600">{hint}</span>}
    </label>
  );
}

// Switch-style toggle (no native checkbox). label + optional hint to the right.
export function Toggle({ checked, onChange, label, hint, disabled = false }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-2 text-left disabled:opacity-50"
    >
      <span className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${checked ? "bg-sky-500/80" : "bg-slate-700"}`}>
        <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
      </span>
      <span className="flex flex-col">
        <span className="text-xs text-slate-200">{label}</span>
        {hint && <span className="text-[10px] text-slate-500">{hint}</span>}
      </span>
    </button>
  );
}

// Segmented control: a bordered container with the active section as a filled pill.
// Reads clearly as navigation (the underline variant was too faint). House idiom,
// matches the Diagnostics / Kubernetes tab bars.
export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="inline-flex flex-wrap gap-0.5 rounded-lg border border-slate-700 bg-slate-900 p-0.5">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
            active === t.id ? "bg-sky-600/25 text-sky-200" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
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

// Collapsible panel for progressive disclosure: same frame as Panel, but the body is
// hidden behind a click. Use to fold away rarely-used / expert controls so the primary
// content stays uncluttered. Closed by default unless `defaultOpen`.
export function Collapsible({ title, hint, defaultOpen = false, right, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-lg border border-slate-700 bg-slate-900">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left transition-colors hover:bg-slate-800/40"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <span className={`text-[10px] text-slate-500 transition-transform duration-150 ${open ? "rotate-90" : ""}`}>▶</span>
          <div>
            <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
            {hint && <p className="text-[11px] text-slate-500">{hint}</p>}
          </div>
        </div>
        {open && right}
      </button>
      {open && <div className="border-t border-slate-800 p-4">{children}</div>}
    </section>
  );
}

// Centered modal dialog: backdrop, Esc / X / click-outside to close, scrollable
// body, body-scroll lock. Use for focused actions (configure, deploy) instead of
// expanding panels inline.
export function Modal({ title, hint, onClose, children, wide }) {
  // Enter/exit transition, self-contained: mount invisible then fade+scale in on the next
  // frame; Escape/backdrop/✕ play the exit (150ms) before calling the real onClose, so the
  // dialog animates both ways without any change in the callers.
  const [shown, setShown] = useState(false);
  const close = useCallback(() => { setShown(false); setTimeout(onClose, 150); }, [onClose]);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setShown(true));
    const onKey = (e) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { cancelAnimationFrame(raf); window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [close]);
  // Portal to <body>: a fixed-position overlay is anchored to the nearest ancestor
  // that establishes a containing block (any ancestor with a transform/filter/
  // will-change, e.g. the .tab-pane rise animation) instead of the viewport, which
  // pins the dialog off-centre and leaves the backdrop not covering the page. Rendering
  // outside the page tree makes `fixed` viewport-relative regardless of ancestors.
  return createPortal(
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm transition-opacity duration-150 ${shown ? "opacity-100" : "opacity-0"}`}
      onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}
    >
      <div className={`flex max-h-[85vh] w-full ${wide ? "max-w-3xl" : "max-w-xl"} flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-2xl transition-all duration-150 ${shown ? "scale-100 opacity-100" : "scale-95 opacity-0"}`}>
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-800 px-5 py-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
            {hint && <p className="mt-0.5 text-[11px] text-slate-500">{hint}</p>}
          </div>
          <button type="button" onClick={close} aria-label="Close" className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">✕</button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>,
    document.body
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
