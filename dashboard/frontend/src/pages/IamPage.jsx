import { useMemo, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { env } from "../runtime-env";
import { KEYCLOAK_AUTHORITY } from "../auth/oidc";

// Static cheat-sheet for the Keycloak realm provisioned by phase 08.
// Read-only: every write action redirects the operator to the Keycloak
// admin console. The dashboard intentionally does not proxy admin write
// endpoints; defense in depth lives at the Keycloak layer.
// See docs/security/iam.md for the full role / endpoint matrix.

const ROLE_MATRIX = [
  {
    role: "dashboard-admin",
    group: "g-dashboard-admins",
    description: "Full read / write on the dashboard.",
    abilities: [
      "Read every dashboard page (overview, topology, metrics, logs, ...)",
      "Restart backend, restart NFs, switch RAN mode",
      "Open pod shells (exec)",
      "Capture traffic (sniffer / pcap)",
      "Manage subscribers (K / OPc visible)",
      "Roll out NF images",
      "Call the CAMARA Location API (composite inherits camara-location-read)",
    ],
  },
  {
    role: "dashboard-viewer",
    group: "g-dashboard-viewers",
    description: "Read-only operator view.",
    abilities: [
      "Read every dashboard page (GET endpoints)",
      "Stream pod logs",
    ],
    restrictions: [
      "Cannot restart, exec, sniff, or modify any resource",
      "Cannot view subscriber K / OPc",
      "Cannot call CAMARA endpoints (unless also placed in g-camara-users)",
    ],
  },
  {
    role: "camara-location-read",
    group: "g-camara-users",
    description: "Orthogonal role for the CAMARA Location API only.",
    abilities: [
      "Call POST /location-retrieval/v0.5/retrieve from the positioning demo",
      "Call POST /location-verification/v3/verify",
    ],
    restrictions: [
      "No access to the dashboard backend (the dashboard requires dashboard-viewer or dashboard-admin)",
    ],
  },
  {
    role: "positioning-edit",
    group: "g-positioning-editors",
    description: "Service-plane EDIT role for authoring room geometry.",
    abilities: [
      "Reach the placement-editor UI through its Keycloak front-door gate",
    ],
    restrictions: [
      "No dashboard backend access (needs dashboard-viewer or dashboard-admin)",
      "g-dashboard-admins also passes the placement-editor gate",
    ],
  },
];

const CLIENTS = [
  { id: "dashboard",           type: "public",       flow: "PKCE (browser)",            note: "Dashboard frontend (this app)." },
  { id: "positioning-demo",    type: "public",       flow: "PKCE (browser)",            note: "Positioning demo SPA." },
  { id: "camara-gateway",      type: "confidential", flow: "client_credentials (M2M)",  note: "CAMARA Northbound gateway service account." },
  { id: "dashboard-readonly",  type: "confidential", flow: "client_credentials (M2M)",  note: "Headless read-only consumer (CI, monitoring agents)." },
  { id: "placement-editor-proxy", type: "confidential", flow: "authorization-code (oauth2-proxy)", note: "Front-door gate for the no-auth placement-editor; admits g-positioning-editors or g-dashboard-admins." },
];

const SEED_USERS = [
  { username: "admin",  group: "g-dashboard-admins",  role: "dashboard-admin",  password: "set on first phase 08 run (force reset on first login)" },
  { username: "viewer", group: "g-dashboard-viewers", role: "dashboard-viewer", password: "same source as admin (force reset on first login)" },
];

function buildKeycloakAdminUrl() {
  if (!KEYCLOAK_AUTHORITY) return null;
  try {
    const u = new URL(KEYCLOAK_AUTHORITY);
    const seg = u.pathname.split("/").filter(Boolean);
    const realmsIdx = seg.indexOf("realms");
    if (realmsIdx < 0) return null;
    const prefix = seg.slice(0, realmsIdx).join("/");
    u.pathname = `/${prefix ? `${prefix}/` : ""}admin/master/console/`;
    u.search = "";
    u.hash = "";
    return u.toString();
  } catch {
    return null;
  }
}

function buildRealmConsoleUrl(realmName) {
  if (!KEYCLOAK_AUTHORITY || !realmName) return null;
  try {
    const u = new URL(KEYCLOAK_AUTHORITY);
    const seg = u.pathname.split("/").filter(Boolean);
    const realmsIdx = seg.indexOf("realms");
    if (realmsIdx < 0) return null;
    const prefix = seg.slice(0, realmsIdx).join("/");
    // The Keycloak admin SPA reads the realm from the URL hash; setting it
    // via u.hash keeps the # literal (assigning it to pathname encodes the
    // # to %23 and the route fails to match).
    u.pathname = `/${prefix ? `${prefix}/` : ""}admin/master/console/`;
    u.search = "";
    u.hash = `/${realmName}`;
    return u.toString();
  } catch {
    return null;
  }
}

function CurlSnippet({ clientId }) {
  const realm = env("VITE_KEYCLOAK_REALM", "5g-testbed");
  const tokenUrl = useMemo(() => {
    if (!KEYCLOAK_AUTHORITY) return null;
    return `${KEYCLOAK_AUTHORITY.replace(/\/$/, "")}/protocol/openid-connect/token`;
  }, []);
  const [copied, setCopied] = useState(false);
  if (!tokenUrl) return null;
  const snippet = `curl -s -X POST ${tokenUrl} \\
  --data-urlencode grant_type=client_credentials \\
  --data-urlencode client_id=${clientId} \\
  --data-urlencode client_secret=<paste-from-.testbed.secrets>`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard write may fail on restrictive contexts; fall back to selecting
      // the text so the operator can copy manually.
    }
  };
  return (
    <div className="mt-1 rounded border border-slate-800 bg-slate-950 p-2 text-[11px] font-mono text-slate-300">
      <pre className="whitespace-pre-wrap break-all">{snippet}</pre>
      <button
        type="button"
        onClick={copy}
        className="mt-1 rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-300 hover:bg-slate-700"
      >
        {copied ? "copied" : "copy"}
      </button>
      <span className="ml-2 text-[10px] text-slate-500">
        realm = {realm}
      </span>
    </div>
  );
}

export default function IamPage() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const realmName = env("VITE_KEYCLOAK_REALM", "5g-testbed");
  const adminUrl = useMemo(buildKeycloakAdminUrl, []);
  const realmConsoleUrl = useMemo(() => buildRealmConsoleUrl(realmName), [realmName]);

  return (
    <div className="flex flex-col gap-6 pb-8">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Identity &amp; Access</h2>
          <p className="text-xs text-slate-400">
            Read-only summary of the Keycloak realm provisioned by phase 08.
            Every write action lives in the Keycloak admin console.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {realmConsoleUrl && (
            <a
              href={realmConsoleUrl}
              target="_blank"
              rel="noreferrer"
              className="rounded bg-indigo-600/20 px-3 py-1.5 text-xs font-medium text-indigo-300 hover:bg-indigo-600/30"
            >
              Open realm console ↗
            </a>
          )}
          {adminUrl && (
            <a
              href={adminUrl}
              target="_blank"
              rel="noreferrer"
              className="rounded bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700"
            >
              Open KC admin (master) ↗
            </a>
          )}
        </div>
      </header>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-200">Realm</h3>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
            <dt className="text-slate-400">Name</dt>
            <dd className="font-mono text-slate-200">{realmName}</dd>
            <dt className="text-slate-400">Authority</dt>
            <dd className="font-mono break-all text-slate-200">{KEYCLOAK_AUTHORITY || "(not configured)"}</dd>
            <dt className="text-slate-400">Issuer</dt>
            <dd className="font-mono break-all text-slate-200">{KEYCLOAK_AUTHORITY || "(not configured)"}</dd>
            <dt className="text-slate-400">Current user</dt>
            <dd className="font-mono text-slate-200">{auth.username || "(none)"}</dd>
            <dt className="text-slate-400">Current roles</dt>
            <dd className="font-mono text-slate-200">{auth.roles.join(", ") || "(none)"}</dd>
          </dl>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-200">Role model</h3>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {ROLE_MATRIX.map((entry) => (
            <article key={entry.role} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs">
              <div className="mb-1 flex items-center justify-between">
                <span className="font-mono text-sm text-indigo-300">{entry.role}</span>
                <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">{entry.group}</span>
              </div>
              <p className="mb-2 text-slate-300">{entry.description}</p>
              <p className="mb-1 text-[10px] uppercase tracking-wide text-emerald-400">Can</p>
              <ul className="mb-2 list-disc space-y-0.5 pl-4 text-slate-300">
                {entry.abilities.map((a) => <li key={a}>{a}</li>)}
              </ul>
              {entry.restrictions && (
                <>
                  <p className="mb-1 text-[10px] uppercase tracking-wide text-rose-400">Cannot</p>
                  <ul className="list-disc space-y-0.5 pl-4 text-slate-300">
                    {entry.restrictions.map((r) => <li key={r}>{r}</li>)}
                  </ul>
                </>
              )}
            </article>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-200">Granting access</h3>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs text-slate-300">
          <p className="mb-2">
            Add a user to a group in the realm console (<span className="font-mono">Open realm console ↗</span>);
            the role follows the group. Groups are orthogonal, so combine them for a persona. You never
            need admin to show someone the positioning demo.
          </p>
          <ul className="space-y-1">
            <li>👁 <b>Positioning demo only</b> (no dashboard) → <span className="font-mono text-slate-200">g-camara-users</span></li>
            <li>🗺 <b>Demo + read-only 5G core view</b> → <span className="font-mono text-slate-200">g-camara-users</span> + <span className="font-mono text-slate-200">g-dashboard-viewers</span></li>
            <li>✏️ <b>Author room geometry</b> (placement-editor) → <span className="font-mono text-slate-200">g-positioning-editors</span></li>
            <li>📊 <b>Read-only operator</b> (dashboard, no writes) → <span className="font-mono text-slate-200">g-dashboard-viewers</span></li>
            <li>🔧 <b>Full control</b> → <span className="font-mono text-slate-200">g-dashboard-admins</span></li>
          </ul>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-200">Seed users (phase 08)</h3>
        <table className="w-full text-xs">
          <thead className="text-left text-slate-400">
            <tr>
              <th className="pb-1 pr-3">Username</th>
              <th className="pb-1 pr-3">Group</th>
              <th className="pb-1 pr-3">Realm role</th>
              <th className="pb-1">Password</th>
            </tr>
          </thead>
          <tbody className="font-mono text-slate-200">
            {SEED_USERS.map((u) => (
              <tr key={u.username} className="border-t border-slate-800">
                <td className="py-1 pr-3">{u.username}</td>
                <td className="py-1 pr-3">{u.group}</td>
                <td className="py-1 pr-3">{u.role}</td>
                <td className="py-1 text-slate-400">{u.password}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[11px] text-slate-500">
          Both seed users are created with <span className="font-mono">temporary: true</span>, forcing
          a password reset at first login. Phase 08 reruns never overwrite a password changed via the
          admin console.
        </p>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-200">OIDC clients</h3>
        <div className="space-y-2">
          {CLIENTS.map((c) => (
            <div key={c.id} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs">
              <div className="mb-1 flex items-center justify-between">
                <span className="font-mono text-sm text-slate-100">{c.id}</span>
                <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                  {c.type} · {c.flow}
                </span>
              </div>
              <p className="text-slate-300">{c.note}</p>
              {isAdmin && c.flow.startsWith("client_credentials") && <CurlSnippet clientId={c.id} />}
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-200">Front-door gate</h3>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs text-slate-300">
          <p className="mb-2">
            Services without native auth (today the <span className="font-mono">placement-editor</span>)
            are fronted by a generic <span className="font-mono">oauth2-proxy</span> gate that performs the
            Keycloak login and admits only the configured groups. The dashboard, demo, and CAMARA gateway
            authenticate on their own and need no gate.
          </p>
          <p>
            The gate sends the browser to the canonical Keycloak issuer while redeeming tokens in-cluster,
            so it behaves the same served locally or behind a tunnel. See the External access doc for the
            routes-versus-subdomains model.
          </p>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-200">Realm reconcile</h3>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs text-slate-300">
          <p className="mb-2">
            The realm JSON is imported by Keycloak only on first boot. Phase 08 includes a
            reconcile step that re-applies redirect URIs, web origins, post-logout URIs, realm
            roles, groups, and composites via the Keycloak admin API.
          </p>
          <p className="mb-2">
            The reconcile gate is interactive: <span className="font-mono">testbed run-phase 08-iam</span>
            asks the operator whether to run reconcile this round (with the option to persist the
            answer to <span className="font-mono">.testbed.env</span>). For CI or scripted runs:
            <span className="font-mono"> KEYCLOAK_REALM_RECONCILE=true testbed run-phase 08-iam</span>.
          </p>
          <p>
            Reconcile leaves users, passwords, and active sessions untouched.
          </p>
        </div>
      </section>
    </div>
  );
}
