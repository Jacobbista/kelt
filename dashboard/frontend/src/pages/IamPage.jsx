import { useMemo, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { env } from "../runtime-env";
import { KEYCLOAK_AUTHORITY } from "../auth/oidc";
import { Link } from "react-router-dom";
import { Collapsible } from "../components/ui";
import { IconArrowLeft } from "../components/icons";
import { getClientSecret, rotateClientSecret, getMasterAdminPassword } from "../api";
import { useToast } from "../context/ToastContext";
import { useConfirm } from "../context/ConfirmContext";

// This console states what THIS realm currently is, plus the two operations the
// dashboard actually owns (revealing and rotating M2M client secrets). The
// conceptual model, the endpoint matrix and the tenancy design belong to the
// docs site; user management belongs to the Keycloak console. Keep prose here
// to one line per item: anything longer is documentation and goes to iam.md.
const IAM_DOCS_URL = `${env("VITE_DOCS_URL", "https://jacobbista.github.io/kelt").replace(/\/+$/, "")}/security/iam/`;

// Role model, one line per role: the full ability/endpoint matrix is iam.md's.
const ROLES = [
  { role: "dashboard-admin", group: "g-dashboard-admins", what: "Full dashboard read/write: exec, sniffer, subscribers, NF rollouts. Inherits camara-location-read." },
  { role: "dashboard-viewer", group: "g-dashboard-viewers", what: "Read-only dashboard: every GET page and log streaming. No writes, no subscriber keys." },
  { role: "camara-location-read", group: "g-camara-users", what: "CAMARA Location API only. No dashboard backend access on its own." },
  { role: "positioning-edit", group: "g-positioning-editors", what: "Passes the placement-editor front-door gate (room geometry authoring). Nothing else." },
];

const CLIENTS = [
  { id: "dashboard",           type: "public",       flow: "PKCE (browser)",            note: "Dashboard frontend (this app)." },
  { id: "positioning-demo",    type: "public",       flow: "PKCE (browser)",            note: "Positioning demo SPA." },
  { id: "camara-gateway",      type: "confidential", flow: "client_credentials (M2M)",  note: "The gateway's own identity and the operator bypass: no org attribute, so its tokens see every tenant. API consumers never use this client.", tokenNote: "operator bypass: this token sees ALL tenants" },
  { id: "camara-api-demo",     type: "confidential", flow: "client_credentials (M2M)",  note: "Reference per-consumer client: the terminal/script path for the CAMARA API. Its service account carries org, so tokens are tenant-scoped. Human users log in via browser only.", tokenNote: "tenant-scoped: this token only sees its own org's assets" },
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
    note: "Tenant. Sees only its own org's assets. Browser login only; its terminal counterpart is the camara-api-demo client.",
  },
];

// The three clients whose secret the dashboard can rotate, with the one thing
// the operator must know per client. placement-editor-proxy is deliberately
// absent: it is infrastructure plumbing, rotated via .testbed.secrets + phase
// 08 (see the note in the Credentials section).
const ROTATABLE = [
  { id: "camara-gateway", impact: "The gateway pod rolls to present the new secret; CAMARA calls keep working." },
  { id: "camara-api-demo", impact: "Hand the new value to whatever calls the CAMARA API as this client." },
  { id: "dashboard-readonly", impact: "Update any CI or monitoring consumer that uses this client." },
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

// Token curl for an M2M client: copy, and optionally substitute the real
// secret (fetched on click only; the reveal is audit-logged server-side).
// Read-only helpers live here; rotation is a separate, heavier operation and
// lives in the Credentials section at the bottom of the page.
function CurlSnippet({ clientId, tokenNote }) {
  const realm = env("VITE_KEYCLOAK_REALM", "5g-testbed");
  const tokenUrl = useMemo(() => {
    if (!KEYCLOAK_AUTHORITY) return null;
    return `${KEYCLOAK_AUTHORITY.replace(/\/$/, "")}/protocol/openid-connect/token`;
  }, []);
  const [copied, setCopied] = useState(false);
  const [secret, setSecret] = useState(null);
  const [secretErr, setSecretErr] = useState("");
  const [lang, setLang] = useState("curl");
  if (!tokenUrl) return null;
  const secretField = secret ? secret : "<paste-from-.testbed.secrets>";
  const snippets = {
    curl: `curl -s -X POST ${tokenUrl} \\
  --data-urlencode grant_type=client_credentials \\
  --data-urlencode client_id=${clientId} \\
  --data-urlencode 'client_secret=${secretField}'`,
    powershell: `$r = Invoke-RestMethod -Method Post -Uri "${tokenUrl}" -Body @{
  grant_type    = 'client_credentials';
  client_id     = '${clientId}';
  client_secret = '${secretField}'
}
$r.access_token`,
    python: `import requests
r = requests.post("${tokenUrl}", data={
    "grant_type": "client_credentials",
    "client_id": "${clientId}",
    "client_secret": "${secretField}",
})
print(r.json()["access_token"])`,
  };
  const snippet = snippets[lang];
  const reveal = async () => {
    setSecretErr("");
    try {
      const res = await getClientSecret(clientId);
      if (res.found) setSecret(res.secret);
      else setSecretErr(`${res.env_key} is not in .testbed.secrets on the host (realm has the changeme default)`);
    } catch (e) {
      setSecretErr(e?.message || "could not read the secret");
    }
  };
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
      <div className="mb-1 flex gap-1">
        {[["curl", "curl"], ["powershell", "PowerShell"], ["python", "Python"]].map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setLang(id)}
            className={`rounded px-2 py-0.5 text-[10px] ${lang === id ? "bg-slate-700 text-slate-100" : "bg-slate-900 text-slate-400 hover:bg-slate-800"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <pre className="whitespace-pre-wrap break-all">{snippet}</pre>
      <button
        type="button"
        onClick={copy}
        className="mt-1 rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-300 hover:bg-slate-700"
      >
        {copied ? "copied" : "copy"}
      </button>
      <button
        type="button"
        onClick={secret ? () => setSecret(null) : reveal}
        className="ml-1 rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-300 hover:bg-slate-700"
      >
        {secret ? "hide secret" : "insert secret"}
      </button>
      <span className="ml-2 text-[10px] text-slate-500">
        realm = {realm}
      </span>
      {secretErr && (
        <span className="ml-2 text-[10px] text-amber-400">{secretErr}</span>
      )}
      {tokenNote && (
        <span className={`ml-2 text-[10px] ${tokenNote.includes("ALL") ? "text-amber-400" : "text-slate-500"}`}>
          {tokenNote}
        </span>
      )}
    </div>
  );
}

// Danger zone: secret rotation, visually and structurally apart from the
// copy-paste helpers above. One row per rotatable client; the fresh secret is
// shown right in the row after a rotation, because handing it to the consumer
// is the very next thing the operator does.
// Read-only reveal of the Keycloak master admin password. Distinct from client
// rotation: this credential is automation-owned (bootstrap + phase 08 + the
// rotations above authenticate with it), auto-generated into .testbed.secrets,
// and never edited by hand or changed from the UI. It is surfaced only so the
// operator can read it to log into the master console without opening the file.
function MasterAdminReveal() {
  const [state, setState] = useState({ shown: false, username: "admin", secret: "", err: "" });
  const [copied, setCopied] = useState(false);
  const reveal = async () => {
    try {
      const res = await getMasterAdminPassword();
      if (res.found) setState({ shown: true, username: res.username, secret: res.secret, err: "" });
      else setState((s) => ({ ...s, err: "KEYCLOAK_ADMIN_PASSWORD is not in .testbed.secrets on the host" }));
    } catch (e) {
      setState((s) => ({ ...s, err: e?.message || "could not read the password" }));
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(state.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // manual selection fallback
    }
  };
  return (
    <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="min-w-[170px] font-medium text-slate-200">Keycloak master admin</span>
        <span className="flex-1 text-[11px] text-slate-400">
          Automation-owned, auto-generated. Read-only: change it neither here nor by editing the file.
          Shown so you can sign in to the master console.
        </span>
        {state.shown ? (
          <span className="flex items-center gap-1.5 rounded bg-slate-950 px-2 py-1 font-mono text-[11px] text-slate-200">
            {state.username} / {state.secret}
            <button type="button" onClick={copy} className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-slate-700">
              {copied ? "copied" : "copy"}
            </button>
          </span>
        ) : (
          <button type="button" onClick={reveal} className="rounded bg-slate-800 px-3 py-1.5 text-[11px] font-medium text-slate-300 hover:bg-slate-700">
            reveal password
          </button>
        )}
      </div>
      {state.err && <p className="mt-1.5 text-[10px] text-amber-400">{state.err}</p>}
    </div>
  );
}

function CredentialsPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const [busy, setBusy] = useState("");
  const [fresh, setFresh] = useState({}); // {clientId: newSecret}
  const [copiedId, setCopiedId] = useState("");

  const rotate = async (id) => {
    const ok = await confirm({
      title: `Rotate the ${id} secret?`,
      body:
        "A new random secret replaces the current one in .testbed.secrets and in Keycloak"
        + (id === "camara-gateway" ? ", and the gateway pod rolls to present it" : "")
        + ". The old secret stops working immediately; already-issued tokens stay valid until they expire (up to 1h).",
      confirmLabel: "Rotate",
      danger: true,
    });
    if (!ok) return;
    setBusy(id);
    try {
      const res = await rotateClientSecret(id);
      setFresh((f) => ({ ...f, [id]: res.secret }));
      toast.success(`${id}: secret rotated${res.pod_synced ? " (gateway pod rolling)" : ""}`);
    } catch (e) {
      toast.error(`${id}: ${e?.message || "rotation failed"}`);
    } finally {
      setBusy("");
    }
  };

  const copyFresh = async (id) => {
    try {
      await navigator.clipboard.writeText(fresh[id]);
      setCopiedId(id);
      setTimeout(() => setCopiedId(""), 1500);
    } catch {
      // Fall back to manual selection of the visible value.
    }
  };

  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold text-slate-200">Credentials</h3>
      <div className="rounded-lg border border-amber-900/50 bg-amber-950/10 p-3">
        <p className="mb-3 text-[11px] leading-relaxed text-slate-400">
          Client secrets live in <span className="font-mono text-slate-300">.testbed.secrets</span> on the host,
          the single source. Rotating generates a new random value, stores it there, applies it to Keycloak,
          and (for the gateway) rolls the pod that presents it. Audit-logged.
        </p>
        <div className="flex flex-col divide-y divide-amber-900/30">
          {ROTATABLE.map((c) => (
            <div key={c.id} className="flex flex-wrap items-center gap-3 py-2.5 text-xs">
              <span className="min-w-[170px] font-mono text-slate-200">{c.id}</span>
              <span className="flex-1 text-[11px] text-slate-400">{c.impact}</span>
              {fresh[c.id] && (
                <span className="flex items-center gap-1.5 rounded bg-slate-950 px-2 py-1 font-mono text-[11px] text-emerald-300">
                  {fresh[c.id]}
                  <button
                    type="button"
                    onClick={() => copyFresh(c.id)}
                    className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-slate-700"
                  >
                    {copiedId === c.id ? "copied" : "copy"}
                  </button>
                </span>
              )}
              <button
                type="button"
                disabled={busy === c.id}
                onClick={() => rotate(c.id)}
                className="rounded bg-amber-600/20 px-3 py-1.5 text-[11px] font-medium text-amber-300 hover:bg-amber-600/30 disabled:opacity-50"
              >
                {busy === c.id ? "rotating…" : "rotate secret"}
              </button>
            </div>
          ))}
        </div>
        <MasterAdminReveal />
        <p className="mt-3 border-t border-amber-900/30 pt-2 text-[10px] text-slate-500">
          Not here on purpose: <span className="font-mono">placement-editor-proxy</span> (infrastructure plumbing:
          new value in the file, then <span className="font-mono">kelt run-phase 08-iam</span> converges Keycloak and
          the oauth2-proxy) and dashboard-login user passwords (managed in Keycloak: admin / viewer / demo reset
          their own).
        </p>
      </div>
    </section>
  );
}

export default function IamPage() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const realmName = env("VITE_KEYCLOAK_REALM", "5g-testbed");
  const adminUrl = useMemo(buildKeycloakAdminUrl, []);
  const realmConsoleUrl = useMemo(() => buildRealmConsoleUrl(realmName), [realmName]);

  return (
    <div className="svc-fade flex flex-col gap-6 pb-8">
        <Link to="/settings" className="inline-flex w-fit items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <IconArrowLeft size={14} /> Settings
        </Link>
      <header className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Identity &amp; Access</h2>
          <p className="text-xs text-slate-400">
            The Keycloak realm provisioned by phase 08. Users are managed in the Keycloak console;
            M2M client credentials are managed here.
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
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
            <dt className="text-slate-400">Realm</dt>
            <dd className="font-mono text-slate-200">{realmName}</dd>
            <dt className="text-slate-400">Authority</dt>
            <dd className="font-mono break-all text-slate-200">{KEYCLOAK_AUTHORITY || "(not configured)"}</dd>
            <dt className="text-slate-400">Signed in as</dt>
            <dd className="font-mono text-slate-200">
              {auth.username || "(none)"}
              <span className="ml-2 text-slate-500">{auth.roles.filter((r) => r.startsWith("dashboard") || r.startsWith("camara") || r.startsWith("positioning")).join(", ")}</span>
            </dd>
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
        <h3 className="mb-2 text-sm font-semibold text-slate-200">Roles and groups</h3>
        <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <table className="w-full text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="pb-1 pr-3">Role</th>
                <th className="pb-1 pr-3">Granted by group</th>
                <th className="pb-1">What it allows</th>
              </tr>
            </thead>
            <tbody className="text-slate-200">
              {ROLES.map((r) => (
                <tr key={r.role} className="border-t border-slate-800 align-top">
                  <td className="py-1.5 pr-3 font-mono text-indigo-300">{r.role}</td>
                  <td className="py-1.5 pr-3 font-mono text-[11px]">{r.group}</td>
                  <td className="py-1.5 text-slate-400">{r.what}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1.5 text-[11px] text-slate-500">
          Full per-endpoint matrix and the tenancy model:{" "}
          <a href={IAM_DOCS_URL} target="_blank" rel="noreferrer" className="text-sky-400 underline">
            IAM documentation ↗
          </a>
        </p>
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
        <p className="mt-1.5 text-[11px] text-slate-500">
          These are dashboard-login users in the <span className="font-mono">5g-testbed</span> realm, each with its own
          throwaway bootstrap password (<span className="font-mono">DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD</span> default{" "}
          <span className="font-mono">kelt-admin</span>, <span className="font-mono">…VIEWER…</span> default{" "}
          <span className="font-mono">kelt-viewer</span>, <span className="font-mono">…TENANT…</span> default{" "}
          <span className="font-mono">kelt-demo</span>). They are NOT the Keycloak master admin
          (<span className="font-mono">KEYCLOAK_ADMIN_PASSWORD</span>, auto-generated, used only by automation). Every
          account forces a reset at first login; later changes are made in Keycloak and survive phase 08 reruns.
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
              {isAdmin && c.flow.startsWith("client_credentials") && <CurlSnippet clientId={c.id} tokenNote={c.tokenNote} />}
            </div>
          ))}
        </div>
      </section>

      {isAdmin && <CredentialsPanel />}

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-slate-200">Guides</h3>

        <Collapsible title="Add a user" hint="Which group grants what, and where to click">
          <div className="space-y-3 text-xs text-slate-300">
            <ol className="list-decimal space-y-1 pl-4">
              <li>Open the realm console (button at the top of this page) → <span className="font-mono text-slate-200">Users</span> → <span className="font-mono text-slate-200">Add user</span>.</li>
              <li><span className="font-mono text-slate-200">Credentials</span> tab → set a password, keep <span className="font-mono text-slate-200">Temporary</span> on.</li>
              <li><span className="font-mono text-slate-200">Groups</span> tab → join one or more groups from the table above. The realm role follows the group.</li>
              <li>For a tenant user only: <span className="font-mono text-slate-200">Attributes</span> tab → add key <span className="font-mono text-slate-200">org</span> with the tenant value. Leaving it empty makes the user an operator that sees every tenant.</li>
            </ol>
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
              <span className="font-mono text-slate-200">kelt run-phase 08-iam</span> asks before running it
              (with an option to persist the answer). Scripted:{" "}
              <span className="font-mono text-slate-200">KEYCLOAK_REALM_RECONCILE=true kelt run-phase 08-iam</span>.
            </p>
          </div>
        </Collapsible>
      </section>
    </div>
  );
}
