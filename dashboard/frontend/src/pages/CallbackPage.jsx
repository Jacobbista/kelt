import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getUserManager } from "../auth/oidc";

// Handles the redirect_uri configured in Keycloak (/auth/callback).
// The OIDC client parses the authorization code from the URL and exchanges
// it for tokens via the realm token endpoint, then navigates back to the
// page the user originally requested.

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
    um.signinRedirectCallback()
      .then((user) => {
        const target = user?.state || "/";
        navigate(target, { replace: true });
      })
      .catch((err) => setError(err?.message || String(err)));
  }, [navigate]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-200">
        <div className="max-w-md rounded-lg border border-rose-700 bg-rose-950/50 p-6">
          <h2 className="mb-2 text-lg font-semibold text-rose-200">Login failed</h2>
          <p className="text-sm text-rose-300">{error}</p>
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
