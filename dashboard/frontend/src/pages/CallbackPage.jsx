import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getUserManager } from "../auth/oidc";

// Handles the redirect_uri configured in Keycloak (/auth/callback).
// The OIDC client parses the authorization code from the URL and exchanges
// it for tokens via the realm token endpoint, then navigates back to the
// page the user originally requested.
//
// Recovery: when the session expires the callback can arrive WITHOUT a usable
// code: an OIDC error response (e.g. Cloudflare Access returning
// temporarily_unavailable / authentication_expired, or login_required), or a
// PKCE state that no longer matches storage ("No matching state found"). Those
// never resolve by sitting on a dead "Login failed" screen, and a hard refresh
// just re-parses the same error. So treat them as "start a fresh login" rather
// than a terminal error, guarded against an immediate redirect loop.

// Loop guard: if we already auto-restarted within this window, stop and surface
// a manual button instead of bouncing forever (e.g. an Access session that
// keeps returning the same error without an interactive prompt).
const RETRY_KEY = "kelt_auth_retry_at";
const RETRY_WINDOW_MS = 15000;
// Any failure at the callback is treated as recoverable: an authorization code
// that cannot be redeemed (already used, expired, "Code not valid" after a
// logout round-trip) never becomes valid by showing the user a dead screen. The
// RETRY_KEY guard below is what prevents this from becoming a redirect loop, so
// the safe default is to start a fresh login and only surface the error if that
// already happened moments ago.
const RECOVERABLE = /./;

export default function CallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState(null);
  // React.StrictMode runs effects twice in development. The second call to
  // signinRedirectCallback redeems an already-consumed authorization code
  // and Keycloak returns 400 invalid_grant. Module-level ref guarantees a
  // single exchange across both mount passes.
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    const um = getUserManager();
    if (!um) {
      navigate("/", { replace: true });
      return;
    }

    // Restart a fresh login unless we just tried (within the guard window).
    const restart = async (reason) => {
      let last = 0;
      try { last = Number(sessionStorage.getItem(RETRY_KEY)) || 0; } catch { /* noop */ }
      if (Date.now() - last < RETRY_WINDOW_MS) {
        setError(reason); // already retried recently: stop, offer the manual path
        return;
      }
      try { sessionStorage.setItem(RETRY_KEY, String(Date.now())); } catch { /* noop */ }
      try {
        await um.signinRedirect();
      } catch (e) {
        setError(e?.message || reason);
      }
    };

    // An error response carries no matching PKCE state, so signinRedirectCallback
    // would dead-end. Restart straight away.
    const params = new URLSearchParams(window.location.search);
    const cbErr = params.get("error");
    if (cbErr) {
      restart(params.get("error_description") || cbErr);
      return;
    }

    um.signinRedirectCallback()
      .then((user) => {
        try { sessionStorage.removeItem(RETRY_KEY); } catch { /* noop */ }
        const target = user?.state || "/";
        navigate(target, { replace: true });
      })
      .catch((err) => {
        const msg = err?.message || String(err);
        if (RECOVERABLE.test(msg)) restart(msg);
        else setError(msg);
      });
  }, [navigate]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-200">
        <div className="max-w-md rounded-lg border border-rose-700 bg-rose-950/50 p-6 text-center">
          <h2 className="mb-2 text-lg font-semibold text-rose-200">Login failed</h2>
          <p className="mb-4 text-sm text-rose-300">{error}</p>
          <button
            onClick={() => {
              try { sessionStorage.removeItem(RETRY_KEY); } catch { /* noop */ }
              const um = getUserManager();
              if (um) um.signinRedirect().catch(() => window.location.assign("/"));
              else window.location.assign("/");
            }}
            className="rounded-md border border-rose-600 bg-rose-900/40 px-4 py-2 text-sm font-medium text-rose-100 hover:bg-rose-900/70"
          >
            Sign in again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
      Completing login…
    </div>
  );
}
