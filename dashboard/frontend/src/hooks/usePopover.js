import { useEffect, useLayoutEffect, useRef, useState } from "react";

// Shared behaviour of the header popovers (account, status, cluster time): placed
// from the trigger's on-screen rect by `place(rect) -> style` and re-placed on
// resize; closed by a click outside or Escape; focus moves to the first control
// inside on open and back to the trigger on Escape. The caller portals the panel
// to <body> (no ancestor's stacking context or overflow can clip it) and renders
// nothing while `style` is null (first frame, before the rect is measured).
export default function usePopover(anchorRef, onClose, place) {
  const ref = useRef(null);
  const [style, setStyle] = useState(null);
  const placeRef = useRef(place);
  placeRef.current = place;
  const placed = style !== null;

  useLayoutEffect(() => {
    const update = () => {
      if (anchorRef?.current) setStyle(placeRef.current(anchorRef.current.getBoundingClientRect()));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [anchorRef]);

  useEffect(() => {
    if (placed) ref.current?.querySelector("button, a[href], select, input")?.focus({ preventScroll: true });
  }, [placed]);

  useEffect(() => {
    const anchor = anchorRef?.current;
    function handleClick(e) {
      if (ref.current?.contains(e.target)) return;
      // The trigger toggles on its own click; closing here too would reopen it.
      if (anchor?.contains(e.target)) return;
      onClose();
    }
    function handleKey(e) {
      if (e.key !== "Escape") return;
      onClose();
      anchor?.focus();
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [onClose, anchorRef]);

  return { ref, style };
}

// Right edge under the trigger: the header's right-hand controls.
export const belowRight = (r) => ({ right: window.innerWidth - r.right, top: r.bottom + 4 });
