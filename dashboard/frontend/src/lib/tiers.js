// Tier colours of an action's trigger (dashboard-design.md, rule 2): CHANGE
// indigo, DISRUPT amber, never grey for DISRUPT. Pure (tiers.test.js).
const PRIMARY = {
  change: "bg-indigo-600/20 text-indigo-300 hover:bg-indigo-600/30",
  disrupt: "bg-amber-600/20 text-amber-300 hover:bg-amber-600/30",
};
const QUIET_TEXT = { change: "text-indigo-300", disrupt: "text-amber-300" };

export function triggerClass(variant, tier) {
  const t = PRIMARY[tier] ? tier : "disrupt";
  if (variant === "quiet") return `rounded bg-slate-700/60 px-2 py-1 text-xs font-medium ${QUIET_TEXT[t]} transition-colors hover:bg-slate-700`;
  return `rounded px-3 py-1.5 text-xs font-medium transition-colors ${PRIMARY[t]}`;
}
