import { useMemo, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { env } from "../runtime-env";
import { KEYCLOAK_AUTHORITY } from "../auth/oidc";
import { Collapsible } from "../components/ui";

// Conceptual background belongs to the docs site, not to this console: the page
// states what THIS realm currently is and how to act on it, and links out for
// the model. See docs/security/iam.md.
const IAM_DOCS_URL = `${env("VITE_DOCS_URL", "https://jacobbista.github.io/kelt").replace(/\/+$/, "")}/security/iam/`;

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
  { id: "camara-gateway",      type: "confidential", flow: "client_credentials (M2M)",  note: "CAMARA Northbound gateway service account. No org attribute, so it sees every tenant." },
  { id: "camara-api-demo",     type: "confidential", flow: "client_credentials (M2M)",  note: "Reference per-consumer CAMARA client. Its service account carries the org attribute, so its tokens are tenant-scoped." },
  { id: "dashboard-readonly",  type: "confidential", flow: "client_credentials (M2M)",  note: "Headless read-only consumer (CI, monitoring agents)." },
  { id: "placement-editor-proxy", type: "confidential", flow: "authorization-code (oauth2-proxy)", note: "Front-door gate for the no-auth placement-editor; admits g-positioning-editors or g-dashboard-admins." },
];

const SEED_USERS = [
  { username: "admin",  groups: "g-dashboard-admins",  role: "dashboard-admin",  org: null, note: "Operator. Full control." },
  { username: "viewer", groups: "g-dashboard-viewers", role: "dashboard-viewer", org: null, note: "Operator. Read-only." },
  {
    username: env("VITE_IAM_TENANT_USER", "demo"),
    groups: "g-camara-users + g-dashboard-viewers",
    role: "camara-location-read + dashboard-viewer",
    org: env("VITE_CAMARA_ORG", "demo"),
    note: "Tenant. Sees only its own org's assets. Created when Northbound is on.",
  },
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
            <dt className="text-slate-400">CAMARA tenant</dt>
            <dd className="font-mono text-slate-200">
              {auth.org
                ? <>org = {auth.org} <span className="text-slate-500">(scoped to this tenant)</span></>
                : <>none <span className="text-slate-500">(operator: sees every tenant)</span></>}
            </dd>
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
        <h3 className="mb-2 text-sm font-semibold text-slate-200">Seed users (phase 08)</h3>
        <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <table className="w-full text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="pb-1 pr-3">Username</th>
                <th className="pb-1 pr-3">Groups</th>
                <th className="pb-1 pr-3">Realm role</th>
                <th className="pb-1 pr-3">org</th>
                <th className="pb-1">What it is for</th>
              </tr>
            </thead>
            <tbody className="text-slate-200">
              {SEED_USERS.map((u) => (
                <tr key={u.username} className="border-t border-slate-800 align-top">
                  <td className="py-1.5 pr-3 font-mono">{u.username}</td>
                  <td className="py-1.5 pr-3 font-mono text-[11px]">{u.groups}</td>
                  <td className="py-1.5 pr-3 font-mono text-[11px]">{u.role}</td>
                  <td className="py-1.5 pr-3 font-mono text-[11px]">
                    {u.org
                      ? <span className="text-indigo-300">{u.org}</span>
                      : <span className="text-slate-500">—</span>}
                  </td>
                  <td className="py-1.5 text-slate-400">{u.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2 rounded border border-slate-800 bg-slate-950/50 p-2.5 text-[11px] text-slate-400">
          <p className="mb-1 font-medium text-slate-300">Initial password</p>
          <ul className="space-y-0.5">
            <li>
              <span className="font-mono text-slate-300">admin</span> — the operator password:{" "}
              <span className="font-mono text-slate-300">KEYCLOAK_ADMIN_PASSWORD</span> from{" "}
              <span className="font-mono text-slate-300">.testbed.secrets</span> on the host, never shown here.
            </li>
            <li>
              <span className="font-mono text-slate-300">viewer</span> — default{" "}
              <span className="font-mono text-slate-300">kelt-viewer</span>, override{" "}
              <span className="font-mono text-slate-300">DASHBOARD_BOOTSTRAP_VIEWER_PASSWORD</span>.
            </li>
            <li>
              <span className="font-mono text-slate-300">demo</span> — default{" "}
              <span className="font-mono text-slate-300">kelt-demo</span>, override{" "}
              <span className="font-mono text-slate-300">DASHBOARD_BOOTSTRAP_TENANT_PASSWORD</span>.
            </li>
          </ul>
          <p className="mt-1">
            The two demo accounts deliberately do not reuse the operator password, since they are the ones
            handed out.
          </p>
          <p className="mt-1">
            Each account is created with <span className="font-mono text-slate-300">temporary: true</span>, so the
            first login forces a reset, and a phase 08 rerun never overwrites a password changed since.
          </p>
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-slate-200">Guides</h3>

        <Collapsible title="Add a user" hint="Which group grants what, and where to click">
          <div className="space-y-3 text-xs text-slate-300">
            <ol className="list-decimal space-y-1 pl-4">
              <li>Open the realm console (button at the top of this page) → <span className="font-mono text-slate-200">Users</span> → <span className="font-mono text-slate-200">Add user</span>.</li>
              <li><span className="font-mono text-slate-200">Credentials</span> tab → set a password, keep <span className="font-mono text-slate-200">Temporary</span> on.</li>
              <li><span className="font-mono text-slate-200">Groups</span> tab → join one or more groups from the table below. The realm role follows the group.</li>
              <li>For a tenant user only: <span className="font-mono text-slate-200">Attributes</span> tab → add key <span className="font-mono text-slate-200">org</span> with the tenant value. Leaving it empty makes the user an operator that sees every tenant.</li>
            </ol>
            <table className="w-full">
              <tbody>
                {[
                  ["g-dashboard-admins", "Full control of the dashboard, and passes the placement-editor gate."],
                  ["g-dashboard-viewers", "Read-only dashboard: every GET page plus log streaming."],
                  ["g-camara-users", "CAMARA Location API and the positioning demo. No dashboard access on its own."],
                  ["g-positioning-editors", "Authoring room geometry in the placement-editor."],
                ].map(([g, what]) => (
                  <tr key={g} className="border-t border-slate-800 align-top">
                    <td className="w-52 py-1 pr-3 font-mono text-slate-200">{g}</td>
                    <td className="py-1 text-slate-400">{what}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-slate-400">
              Groups combine: demo plus a read-only core view is{" "}
              <span className="font-mono text-slate-200">g-camara-users</span> +{" "}
              <span className="font-mono text-slate-200">g-dashboard-viewers</span>, which is exactly the seed
              tenant user above.
            </p>
          </div>
        </Collapsible>

        <Collapsible title="Front-door gate" hint="How services without native auth are protected">
          <p className="text-xs text-slate-300">
            The <span className="font-mono">placement-editor</span> has no login of its own, so it sits behind a
            generic <span className="font-mono">oauth2-proxy</span> gate that performs the Keycloak login and
            admits only <span className="font-mono">g-positioning-editors</span> or{" "}
            <span className="font-mono">g-dashboard-admins</span>. The dashboard, the demo, and the CAMARA gateway
            authenticate on their own and are not gated.
          </p>
        </Collapsible>

        <Collapsible title="Realm reconcile" hint="Propagating realm template edits to a running cluster">
          <div className="space-y-2 text-xs text-slate-300">
            <p>
              Keycloak imports the realm JSON only on first boot. Reconcile re-applies redirect URIs, web origins,
              roles, groups, and composites through the admin API, and leaves users, passwords, and sessions untouched.
            </p>
            <p className="text-slate-400">
              <span className="font-mono text-slate-200">testbed run-phase 08-iam</span> asks before running it
              (with an option to persist the answer). Scripted:{" "}
              <span className="font-mono text-slate-200">KEYCLOAK_REALM_RECONCILE=true testbed run-phase 08-iam</span>.
            </p>
          </div>
        </Collapsible>
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

      <p className="text-[11px] text-slate-500">
        Full role and endpoint matrix, and the tenancy model, in the{" "}
        <a href={IAM_DOCS_URL} target="_blank" rel="noreferrer" className="text-sky-400 underline">
          IAM documentation ↗
        </a>.
      </p>
    </div>
  );
}
