import React, { useState } from "react";
import CallbackPage from "../pages/CallbackPage";
import { env } from "../runtime-env";
import { summarizeStatus } from "../lib/backendStatus";

// The gate: the one frame for every state where the shell cannot be used
// (lib/authFlow.js gateView). Before the shell it fills the screen; when the
// backend is lost with the shell open it covers it (over), leaving the pages
// mounted underneath so they come back with their data.

// Radius r, arc lengths in index.css: 2*pi*24 = 151, 2*pi*17 = 107, 2*pi*10 = 63.
function Orbit() {
  return (
    <svg className="gate__orbit" viewBox="0 0 56 56" aria-hidden="true">
      {[24, 17, 10].map((r) => <circle key={r} className="track" cx="28" cy="28" r={r} />)}
      <circle className="arc arc-1" cx="28" cy="28" r="24" />
      <circle className="arc arc-2" cx="28" cy="28" r="17" />
      <circle className="arc arc-3" cx="28" cy="28" r="10" />
      <circle className="core" cx="28" cy="28" r="3.5" />
    </svg>
  );
}

function GateScreen({ title, body, busy = false, over = false, leaving = false, wide = false, children, hint }) {
  return (
    <div className={`gate${over ? " gate--over" : ""}${leaving ? " is-leaving" : ""}`} role="status" aria-live="polite">
      <div className={`gate__card${wide ? " gate__card--wide" : ""}`}>
        {busy ? <Orbit /> : <img src="/kelt-mark.svg" alt="" className="gate__mark" />}
        <h1 className="gate__title">{title}</h1>
        {body && <p className="gate__subtitle">{body}</p>}
        {children}
        {hint && <p className="gate__hint">{hint}</p>}
      </div>
    </div>
  );
}

function Actions({ children }) {
  return <div className="gate__actions">{children}</div>;
}

function Button({ kind, ...props }) {
  return <button type="button" className={`gate__button${kind ? ` gate__button--${kind}` : ""}`} {...props} />;
}

const WORD = { up: "ready", down: "not answering", unknown: "checking", "access-expired": "sign-in needed" };

function ServiceList({ items }) {
  return (
    <ul className="gate__list">
      {items.map(({ label, state }) => (
        <li key={label} className={state === "up" ? "ok" : "wait"}>
          <span className="dot" /> {label}
          <span className="status">{WORD[state] || state}</span>
        </li>
      ))}
    </ul>
  );
}

function InsecureOrigin() {
  const secure = env("VITE_SECURE_URL");
  const here = window.location.pathname + window.location.search;
  return (
    <GateScreen
      title="Sign-in needs HTTPS"
      body={`This address (${window.location.origin}) is plain HTTP, and the browser does not allow the login there.`}
      hint={secure ? null : "Open the dashboard through its HTTPS address, or on localhost."}
    >
      {secure && (
        <Actions>
          <a href={`${secure.replace(/\/+$/, "")}${here}`} className="gate__button gate__button--primary">
            Open {secure.replace(/^https:\/\//, "").replace(/\/+$/, "")}
          </a>
        </Actions>
      )}
    </GateScreen>
  );
}

function StatusPanel({ watchdog }) {
  if (watchdog.loading && !watchdog.status) return <p className="gate__hint">Reading the service status…</p>;
  const { state, messages, error } = summarizeStatus(watchdog.status);
  if (error) return <p className="gate__note gate__note--warn">{error}</p>;
  return (
    <div className="gate__status">
      {state && (
        <p className="gate__status-state">
          <span className="gate__status-label">Service</span> {state}
        </p>
      )}
      {messages.length > 0 ? (
        <ul className="gate__msgs">
          {messages.map((m, i) => (
            <li key={i} className={`gate__msg gate__msg--${m.level}`}>
              <span className="gate__msg-time">{m.time}</span>
              <span className="gate__msg-text">{m.text}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="gate__hint">No recent messages from the service.</p>
      )}
    </div>
  );
}

// What can be done while the backend does not answer: read the service state
// and, for an admin, restart it through the watchdog. Shared by the cover over
// the shell and by the start screen (a reload while the backend is down lands
// there). No reload button: it only brings the page back here.
function BackendControls({ watchdog, isAdmin, recheck }) {
  const [open, setOpen] = useState(false);
  // idle | restarting | requested | failed
  const [restart, setRestart] = useState({ phase: "idle" });

  const onRestart = async () => {
    setRestart({ phase: "restarting" });
    const res = await watchdog.restart();
    if (res.error) { setRestart({ phase: "failed", error: res.error }); return; }
    setRestart({ phase: "requested" });
    // Probe at once and then quickly: the gate goes away on the first answer.
    for (const ms of [1500, 1500, 2000, 3000]) {
      await new Promise((r) => setTimeout(r, ms));
      if (await recheck()) return;
    }
    setRestart({ phase: "failed", error: "Restarted, but the backend has not answered yet. Its status says why." });
    setOpen(true);
    watchdog.fetchStatus();
  };

  return (
    <>
      {open && <StatusPanel watchdog={watchdog} />}
      {restart.phase === "requested" && <p className="gate__note">Restart requested. Waiting for the backend to answer…</p>}
      {restart.phase === "failed" && <p className="gate__note gate__note--warn">{restart.error}</p>}
      <Actions>
        <Button onClick={() => { if (!open) watchdog.fetchStatus(); setOpen(!open); }}>
          {open ? "Hide status" : "Show status"}
        </Button>
        {isAdmin && (
          <Button kind="warn" onClick={onRestart} disabled={restart.phase === "restarting" || restart.phase === "requested"}>
            {restart.phase === "restarting" ? "Restarting…" : restart.phase === "requested" ? "Restarted" : "Restart backend"}
          </Button>
        )}
      </Actions>
    </>
  );
}

export default function AuthGate({ view, auth, backend, iam, watchdog, isAdmin, recheck, leaving = false }) {
  const reload = <Actions><Button kind="primary" onClick={() => window.location.reload()}>Reload</Button></Actions>;
  switch (view) {
    case "insecure":
      return <InsecureOrigin />;
    case "callback":
      return <CallbackPage Screen={GateScreen} Actions={Actions} Button={Button} />;
    case "backend-lost":
      return (
        <GateScreen
          over
          leaving={leaving}
          busy
          title="Backend not reachable"
          body="Reconnecting. The page comes back by itself as soon as the backend answers."
          hint="systemd restarts the backend by itself when it stops on its own; it stays down only after a manual stop or when it cannot start."
        >
          <BackendControls watchdog={watchdog} isAdmin={isAdmin} recheck={recheck} />
        </GateScreen>
      );
    case "signing-out":
      return <GateScreen busy title="Signing out" />;
    case "redirecting":
      return <GateScreen busy title="Opening the sign-in page" />;
    case "ended":
      return (
        <GateScreen
          title="Session ended"
          body="The dashboard session expired while this page was open. Sign in again to go on where you were."
        >
          <Actions><Button kind="primary" onClick={() => auth.login()}>Sign in</Button></Actions>
        </GateScreen>
      );
    case "signed-out":
      return (
        <GateScreen title="Signed out" body="The dashboard session has ended. Signing in again goes back to the page you left.">
          <Actions><Button kind="primary" onClick={() => auth.login()}>Sign in again</Button></Actions>
        </GateScreen>
      );
    case "access-expired":
      return (
        <GateScreen
          title="Access session expired"
          body="The access session in front of the dashboard has expired. Reload the page to sign in again."
          hint="The perimeter gate (Cloudflare Access) answered with its login page; a background check cannot sign in for you."
        >
          {reload}
        </GateScreen>
      );
    default: {
      const items = [{ label: "Dashboard API", state: backend }];
      if (auth.enabled) {
        items.push({ label: "Identity (Keycloak)", state: iam });
        items.push({ label: "Session", state: !auth.loading && auth.user ? "up" : "unknown" });
      }
      return (
        <GateScreen
          busy
          title="5G Dashboard"
          body="Waiting for the services to answer."
          hint="After a reset the first start can take up to 2 minutes while the pods come up."
        >
          <ServiceList items={items} />
          {/* The backend is down with a session already open (a reload during
              an outage): the same controls as the cover over the shell. */}
          {backend === "down" && (!auth.enabled || auth.user) && (
            <BackendControls watchdog={watchdog} isAdmin={isAdmin} recheck={recheck} />
          )}
        </GateScreen>
      );
    }
  }
}
