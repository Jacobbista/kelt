// Whether the backend is known to be down (hooks/useBackendHealth.js sets it).
// While it is, the gate covers the pages and api.js holds their reads: a read
// is sent when the backend answers again, so a page keeps its data and its
// state and refreshes on recovery, instead of showing the error of a request
// that could only fail (and adding it to the browser console). Reads of the same
// path wait on one request, so a page polling every few seconds through a long
// outage sends one read on recovery, not one per tick.
let down = false;
let waiters = [];
const held = new Map();

export function setBackendDown(value) {
  down = !!value;
  if (!down) {
    const ready = waiters;
    waiters = [];
    ready.forEach((resolve) => resolve());
  }
}

export function backendDown() {
  return down;
}

export function whenBackendUp() {
  if (!down) return Promise.resolve();
  return new Promise((resolve) => waiters.push(resolve));
}

// send() at once when the backend is up; when it is down, after it is back,
// once per key for every caller waiting meanwhile.
export function holdWhileDown(key, send) {
  if (!down) return send();
  if (!held.has(key)) {
    const p = whenBackendUp().then(send).finally(() => held.delete(key));
    held.set(key, p);
  }
  return held.get(key);
}
