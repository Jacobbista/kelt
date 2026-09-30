// When the dashboard sends the browser to Keycloak's login page by itself: on a
// first visit with no session. A session that ended while the page was open
// waits for a click instead, so the login page opens when someone is there to
// use it: Keycloak's page expires after 30 minutes, and one left open in an
// unattended tab failed on the first submit.
export function autoLogin({ enabled, loading, user, loggingOut, ended, path }) {
  return !!enabled && !loading && !user && !loggingOut && !ended
    && path !== "/auth/callback" && path !== "/logged-out";
}
