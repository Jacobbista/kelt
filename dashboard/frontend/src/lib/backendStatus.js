// What the gate shows of the watchdog's /status (dashboard/backend/watchdog.py)
// when the backend does not answer: the service state and the last messages
// that say something, not the raw journal. sudo and PAM lines (the backend's own
// systemctl calls) and HTTP access lines are noise here.
const MAX_MESSAGES = 8;
const NOISE = [/ sudo\[\d+\]:/, /pam_unix\(/, /"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S+ HTTP\/[\d.]+"/];
// 2026-10-07T15:21:14+0000 host unit[pid]: LEVEL:    message
const LINE = /^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\S*\s+\S+\s+[^:]+:\s*(?:(DEBUG|INFO|WARNING|ERROR|CRITICAL):\s*)?(.*)$/;

export function summarizeStatus(status) {
  if (status?.error) return { state: null, messages: [], error: status.error };
  const active = (status?.status_output || "").split("\n").find((l) => l.trim().startsWith("Active:"));
  const messages = [];
  for (const raw of (status?.journal || "").split("\n")) {
    if (!raw.trim() || NOISE.some((re) => re.test(raw))) continue;
    const m = LINE.exec(raw);
    if (!m) continue;
    messages.push({ time: m[1], level: (m[2] || "info").toLowerCase(), text: m[3].trim() });
  }
  return {
    state: active ? active.trim().replace(/^Active:\s*/, "") : null,
    messages: messages.slice(-MAX_MESSAGES),
  };
}
