// A moment in a compact list (the header's operations, …): today only the time,
// so an old entry never reads as today's; yesterday says so; earlier the day
// and month. Local time, 24 h.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");

export function shortWhen(iso, now = new Date()) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const dayStart = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((dayStart(now) - dayStart(d)) / 86400000);
  if (days === 0) return time;
  if (days === 1) return `yesterday ${time}`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${time}`;
}
