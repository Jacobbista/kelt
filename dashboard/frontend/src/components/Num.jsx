import React, { useEffect, useRef, useState } from "react";
import { isSettled, placeholder, randomDigit, scrambleFrame } from "../lib/digits";

const reduceMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// A number that resolves in place. value == null: `width` digits cycle at the
// value's final width. A known value on first render shows at once (it came
// from the cache); a later change cycles only the digits that changed.
export default function Num({ value, unit, format = (v) => String(v), width = 1, className = "" }) {
  const final = value == null ? null : format(value);
  const shown = useRef(final);
  const [text, setText] = useState(() => final ?? (reduceMotion() ? "–" : placeholder(width)));
  const [phase, setPhase] = useState(final == null ? "unknown" : "settled");

  useEffect(() => {
    if (reduceMotion()) {
      setText(final ?? "–"); shown.current = final; setPhase("settled");
      return undefined;
    }
    if (final == null) {
      shown.current = null; // a value that comes back resolves again
      setPhase("unknown");
      const id = setInterval(() => setText(placeholder(width)), 60);
      return () => clearInterval(id);
    }
    if (final === shown.current) return undefined;
    const prev = shown.current;
    let t = 0;
    setPhase("scrambling");
    const id = setInterval(() => {
      t += 1;
      if (isSettled(final, t)) {
        clearInterval(id);
        shown.current = final;
        setText(final);
        setPhase("changed");
        return;
      }
      setText(scrambleFrame(prev, final, t, randomDigit));
    }, 45);
    return () => clearInterval(id);
  }, [final, width]);

  const cls = phase === "unknown" || phase === "scrambling" ? "num-scrambling" : phase === "changed" ? "num-changed" : "";
  return (
    <span className={`font-mono tabular-nums ${cls} ${className}`}>
      {text}
      {unit && <span className="ml-1 font-sans text-xs text-slate-400">{unit}</span>}
    </span>
  );
}
