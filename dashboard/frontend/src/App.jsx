import React, { useEffect, useState } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { getRuntimeInfo } from "./api";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ConfirmProvider } from "./context/ConfirmContext";
import { UpdateProvider } from "./context/UpdateContext";
import { IsolationSummaryProvider } from "./context/IsolationSummaryContext";
import { StatusSummaryProvider } from "./context/StatusSummaryContext";
import { useBackendHealth } from "./hooks/useBackendHealth";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./Layout";
import { ROUTES } from "./navigation";
import { env } from "./runtime-env";
import LogViewer from "./components/LogViewer";
import PodTerminal from "./components/PodTerminal";
import CallbackPage from "./pages/CallbackPage";
import CorePage from "./pages/CorePage";
import HealthPage from "./pages/HealthPage";
import CapturePage from "./pages/CapturePage";
import IsolationPage from "./pages/IsolationPage";
import SettingsPage from "./pages/SettingsPage";
import IamPage from "./pages/IamPage";
import BrandingPage from "./pages/BrandingPage";
import StoragePage from "./pages/StoragePage";
import KubernetesPage from "./pages/KubernetesPage";
import LoggedOutPage from "./pages/LoggedOutPage";
import MetricsPage from "./pages/MetricsPage";
import NorthboundPage from "./pages/NorthboundPage";
import NorthboundAssetsPage from "./pages/NorthboundAssetsPage";
import OverviewPage from "./pages/OverviewPage";
import ServicesPage from "./pages/ServicesPage";
import CustomWorkloadPage from "./pages/CustomWorkloadPage";
import AppsPage from "./pages/AppsPage";
import ManualPage from "./pages/ManualPage";
import OperationsPage from "./pages/OperationsPage";
import OperationsSettingsPage from "./pages/OperationsSettingsPage";
import RanPage from "./pages/RanPage";
import SubscribersPage from "./pages/SubscribersPage";
import TopologyPage from "./pages/TopologyPage";
import UEMonitoringPage from "./pages/UEMonitoringPage";
import { autoLogin } from "./lib/authFlow";


export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}

// Route guard for pages whose backend routers are admin-only end to end.
// Hiding the sidebar entry is not enough: the URL is still typeable, and the
// page would then fire a burst of requests that all come back 403. Enforcement
// stays in the backend; this only decides what the browser bothers to render.
function AdminOnly({ children }) {
  const auth = useAuth();
  if (!auth.enabled || auth.roles.includes("dashboard-admin")) return children;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-900/60 p-6">
      <h2 className="text-lg font-semibold text-slate-200">Not available with your role</h2>
      <p className="text-sm text-slate-400">
        This page needs the <span className="font-mono text-slate-300">dashboard-admin</span> role.
        Your account is read-only.
      </p>
    </div>
  );
}

// The session ended while the page was open (lib/authFlow.js): the login page
// opens on the click, fresh, and the user comes back to the same page.
function SessionEnded({ onSignIn }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-300">
      <div className="max-w-md rounded-lg border border-slate-700 bg-slate-900 p-6">
        <h2 className="mb-2 text-lg font-semibold text-slate-100">Session ended</h2>
        <p className="text-sm leading-relaxed text-slate-400">
          The dashboard session expired while this page was open. Sign in again to go on where you were.
        </p>
        <button type="button" onClick={() => onSignIn()} className="mt-4 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">
          Sign in
        </button>
      </div>
    </div>
  );
}

// Sign-in cannot start on a plain-HTTP origin (see AuthContext). Say so and
// point at the HTTPS address of this same frontend, keeping the path.
function InsecureOrigin() {
  const secure = env("VITE_SECURE_URL");
  const here = window.location.pathname + window.location.search;
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-300">
      <div className="max-w-md rounded-lg border border-slate-700 bg-slate-900 p-6">
        <h2 className="mb-2 text-lg font-semibold text-slate-100">Sign-in needs HTTPS</h2>
        <p className="text-sm leading-relaxed text-slate-400">
          This address ({window.location.origin}) is plain HTTP, and the browser does not allow the
          login there.
        </p>
        {secure ? (
          <a href={`${secure.replace(/\/+$/, "")}${here}`} className="mt-4 inline-block rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">
            Open {secure.replace(/^https:\/\//, "").replace(/\/+$/, "")}
          </a>
        ) : (
          <p className="mt-3 text-sm text-slate-400">Open the dashboard through its HTTPS address, or on localhost.</p>
        )}
      </div>
    </div>
  );
}

function AppInner() {
  const navigate = useNavigate();
  const auth = useAuth();
  const [runtime, setRuntime] = useState({ mode: "unknown", runtime_source: "unknown" });
  const [logTarget, setLogTarget] = useState(null);
  const [termTarget, setTermTarget] = useState(null);
  const { unreachable: backendUnreachable, sessionExpired, serverTime } = useBackendHealth();

  useEffect(() => {
    // With no session, go to Keycloak on a first visit; a session that ended
    // while the page was open waits for "Sign in" (lib/authFlow.js). The
    // auth/callback route handles the return trip; /logged-out renders its own
    // "sign in again" so an explicit logout does not bounce straight back
    // through the still-alive Keycloak SSO session.
    if (autoLogin({ ...auth, path: window.location.pathname })) auth.login();
  }, [auth.enabled, auth.loading, auth.user, auth.loggingOut, auth.ended, auth.login]);

  useEffect(() => {
    // Avoid firing while the auth context is still resolving an existing
    // session; otherwise the request races the login redirect and 401s.
    if (auth.enabled && (auth.loading || !auth.user)) return;
    getRuntimeInfo()
      .then((data) =>
        setRuntime({
          mode: (data.mode || "unknown").toLowerCase(),
          runtime_source: data.runtime_source || "unknown",
        })
      )
      .catch(() => {});
  }, [auth.enabled, auth.loading, auth.user]);

  function handleOpenLogs(nf) {
    setLogTarget({ name: nf.name, containers: nf.containers, namespace: nf.namespace, deployment: nf.deployment });
  }

  function handleOpenTerminal(nf) {
    setTermTarget({ name: nf.name, containers: nf.containers, nfType: nf.nf_type, namespace: nf.namespace });
  }

  function handleOpenIperf3Logs(nf) {
    setTermTarget({
      name: nf.name,
      containers: nf.containers,
      nfType: nf.nf_type,
      namespace: nf.namespace,
      command: "tail -F /var/log/iperf3-server.log",
      title: "iperf3 Server Logs",
    });
  }

  function onNavigate(id) {
    navigate(ROUTES[id] ?? "/");
  }

  // While auth is enabled and the session is unresolved or absent, do not mount
  // the app: the redirect to Keycloak is already in flight (effect above).
  // Rendering pages here would fire API calls with no token and flash a 401
  // banner before the redirect lands. The callback and logged-out routes must
  // still render to drive their own flow, so they are exempt.
  const authPath = window.location.pathname;
  if (auth.insecureOrigin) return <InsecureOrigin />;
  if (
    auth.enabled
    && authPath !== "/auth/callback"
    && authPath !== "/logged-out"
    && (auth.loading || !auth.user)
  ) {
    if (auth.ended && !auth.loading && !auth.loggingOut) return <SessionEnded onSignIn={auth.login} />;
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        {auth.loggingOut ? "Signing out…" : "Signing in…"}
      </div>
    );
  }

  return (
    <ErrorBoundary>
    <ToastProvider>
    <ConfirmProvider>
    <UpdateProvider>
    <IsolationSummaryProvider>
    <StatusSummaryProvider>
    <Layout
      onNavigate={onNavigate}
      runtime={runtime}
      serverTime={serverTime}
      backendUnreachable={backendUnreachable}
      sessionExpired={sessionExpired}
    >
      <Routes>
        <Route path="/auth/callback" element={<CallbackPage />} />
        <Route path="/logged-out" element={<LoggedOutPage />} />
        <Route path="/" element={<OverviewPage />} />
        <Route path="/kubernetes" element={<KubernetesPage />} />
        <Route path="/core" element={
          <CorePage onOpenLogs={handleOpenLogs} onOpenTerminal={handleOpenTerminal} onOpenIperf3Logs={handleOpenIperf3Logs} />
        } />
        <Route path="/network/topology" element={<TopologyPage />} />
        <Route path="/network/isolation" element={<IsolationPage />} />
        <Route path="/network/health" element={<HealthPage />} />
        <Route path="/network/capture" element={<AdminOnly><CapturePage /></AdminOnly>} />
        <Route path="/topology" element={<Navigate to="/network/topology" replace />} />
        <Route path="/diagnostics" element={<Navigate to="/network/health" replace />} />
        <Route path="/ran" element={<AdminOnly><RanPage /></AdminOnly>} />
        <Route path="/subscribers" element={<AdminOnly><SubscribersPage /></AdminOnly>} />
        <Route path="/ue-monitor" element={<UEMonitoringPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
        <Route path="/operations" element={<AdminOnly><OperationsPage /></AdminOnly>} />
        <Route path="/services" element={<ServicesPage />} />
        <Route path="/services/northbound" element={<NorthboundPage />} />
        <Route path="/services/northbound/assets" element={<AdminOnly><NorthboundAssetsPage /></AdminOnly>} />
        <Route path="/services/custom" element={<CustomWorkloadPage />} />
        <Route path="/services/apps" element={<AppsPage />} />
        <Route path="/northbound" element={<Navigate to="/services/northbound" replace />} />
        <Route path="/settings" element={<AdminOnly><SettingsPage /></AdminOnly>} />
        <Route path="/settings/iam" element={<AdminOnly><IamPage /></AdminOnly>} />
        <Route path="/settings/branding" element={<AdminOnly><BrandingPage /></AdminOnly>} />
        <Route path="/settings/storage" element={<AdminOnly><StoragePage /></AdminOnly>} />
        <Route path="/settings/operations" element={<AdminOnly><OperationsSettingsPage /></AdminOnly>} />
        <Route path="/iam" element={<Navigate to="/settings/iam" replace />} />
        <Route path="/branding" element={<Navigate to="/settings/branding" replace />} />
        <Route path="/manual" element={<ManualPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      {logTarget && (
        <LogViewer
          namespace={logTarget.namespace}
          pod={logTarget.name}
          containers={logTarget.containers}
          container={logTarget.containers?.[0]}
          deployment={logTarget.deployment}
          onClose={() => setLogTarget(null)}
        />
      )}

      {termTarget && (
        <PodTerminal
          namespace={termTarget.namespace}
          pod={termTarget.name}
          containers={termTarget.containers}
          container={termTarget.containers?.[0]}
          nfType={termTarget.nfType}
          command={termTarget.command}
          title={termTarget.title}
          onClose={() => setTermTarget(null)}
        />
      )}
    </Layout>
    </StatusSummaryProvider>
    </IsolationSummaryProvider>
    </UpdateProvider>
    </ConfirmProvider>
    </ToastProvider>
    </ErrorBoundary>
  );
}
