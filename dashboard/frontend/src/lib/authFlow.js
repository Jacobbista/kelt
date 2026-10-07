const AUTH_ROUTES = ["/auth/callback", "/logged-out"];

// What the gate (components/AuthGate.jsx) shows, in front of the shell or over
// it. The shell opens only once the backend and Keycloak have answered and there
// is a session (auth on); until then one full-screen state, never a page inside
// the shell. After it has opened, Keycloak is no longer watched, and a backend
// that goes down covers the shell ("backend-lost") without unmounting it, so the
// pages come back with their data. An expired perimeter session (Cloudflare
// Access) closes everything: no request can succeed until a reload.
//
// A first visit with no session goes to Keycloak by itself ("redirecting"); a
// session that ended while the page was open waits for a click ("ended"), so the
// login page opens when someone is there to use it: Keycloak's page expires
// after 30 minutes, and one left open in an unattended tab failed on submit.
//
// backend, iam: "unknown" | "up" | "down" | "access-expired".
export function gateView({ enabled, insecureOrigin, loading, user, loggingOut, ended, path, backend, iam, appShown }) {
  if (enabled && insecureOrigin) return "insecure";
  if (backend === "access-expired" || (!appShown && enabled && iam === "access-expired")) return "access-expired";
  if (appShown) {
    if (enabled && loggingOut) return "signing-out";
    if (enabled && !user) return ended ? "ended" : "signed-out";
    return backend === "down" ? "backend-lost" : "app";
  }
  if (enabled && path === "/auth/callback") return "callback";
  if (enabled && loggingOut) return "signing-out";
  if (backend !== "up" || (enabled && (iam !== "up" || loading))) return "starting";
  if (enabled && !user) {
    if (path === "/logged-out") return "signed-out";
    return ended ? "ended" : "redirecting";
  }
  return "app";
}

// Where a login brings the user back: the page they are on, or, from an auth
// route, the page they left at logout (stored), never an auth route itself.
export function returnPath(path, stored) {
  if (path && !AUTH_ROUTES.includes(path.split("?")[0])) return path;
  return stored && !AUTH_ROUTES.includes(stored.split("?")[0]) ? stored : "/";
}
