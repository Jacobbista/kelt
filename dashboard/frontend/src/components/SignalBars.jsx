import React from "react";

// The dashboard's one loader (Foundations spec, 5): four signal bars, 16 px.
// Callers put it in a card header slot; it carries no label or timer.
export default function SignalBars() {
  return (
    <span className="bars" aria-hidden="true"><i /><i /><i /><i /></span>
  );
}
