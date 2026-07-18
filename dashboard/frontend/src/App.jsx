import React, { useEffect, useState } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { getRuntimeInfo } from "./api";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { OperationsProvider } from "./context/OperationsContext";
import { ToastProvider } from "./context/ToastContext";
import { ConfirmProvider } from "./context/ConfirmContext";
import { useBackendHealth } from "./hooks/useBackendHealth";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./Layout";
import LogViewer from "./components/LogViewer";
import PodTerminal from "./components/PodTerminal";
import CallbackPage from "./pages/CallbackPage";
import CorePage from "./pages/CorePage";
import DiagnosticsPage from "./pages/DiagnosticsPage";
import SettingsPage from "./pages/SettingsPage";
import KubernetesPage from "./pages/KubernetesPage";
import LoggedOutPage from "./pages/LoggedOutPage";
import MetricsPage from "./pages/MetricsPage";
import NorthboundPage from "./pages/NorthboundPage";
import OverviewPage from "./pages/OverviewPage";
import ServicesPage from "./pages/ServicesPage";
import CustomWorkloadPage from "./pages/CustomWorkloadPage";
import AppsPage from "./pages/AppsPage";
import ManualPage from "./pages/ManualPage";
import RanPage from "./pages/RanPage";
import SubscribersPage from "./pages/SubscribersPage";
import TopologyPage from "./pages/TopologyPage";
import UEMonitoringPage from "./pages/UEMonitoringPage";

const ROUTES = {
  overview:       "/",
  kubernetes:     "/kubernetes",
  core:           "/core",
  topology:       "/topology",
  ran:            "/ran",
  subscribers:    "/subscribers",
  "ue-monitoring": "/ue-monitor",
  diagnostics:    "/diagnostics",
  metrics:        "/metrics",
  services:       "/services",
  settings:       "/settings",
  manual:         "/manual",
};

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

function AppInner() {
  const navigate = useNavigate();
  const auth = useAuth();
  const [runtime, setRuntime] = useState({ mode: "unknown", runtime_source: "unknown" });
  const [logTarget, setLogTarget] = useState(null);
  const [termTarget, setTermTarget] = useState(null);
  const [expandNfType, setExpandNfType] = useState(null);
  const { unreachable: backendUnreachable, sessionExpired, serverTime } = useBackendHealth();

  useEffect(() => {
    // When auth is enabled but the user is not signed in, redirect to
    // Keycloak immediately. The auth/callback route handles the return
    // trip; the /logged-out landing renders a manual "sign in again"
    // button so an explicit logout does not bounce straight back through
    // the still-alive Keycloak SSO session.
    const path = window.location.pathname;
    if (
      auth.enabled
      && !auth.loading
      && !auth.user
      && path !== "/auth/callback"
      && path !== "/logged-out"
    ) {
      auth.login();
    }
  }, [auth.enabled, auth.loading, auth.user, auth.login]);

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

  function handleNavigateToNf(nfType) {
    setExpandNfType(nfType);
    navigate("/core");
  }

  function handleOpenLogs(nf) {
    setLogTarget({ name: nf.name, containers: nf.containers, namespace: "5g", deployment: nf.deployment });
  }

  function handleOpenTerminal(nf) {
    setTermTarget({ name: nf.name, containers: nf.containers, nfType: nf.nf_type, namespace: "5g" });
  }

  function handleOpenIperf3Logs(nf) {
    setTermTarget({
      name: nf.name,
      containers: nf.containers,
      nfType: nf.nf_type,
      namespace: "5g",
      command: "tail -F /var/log/iperf3-server.log",
      title: "iperf3 Server Logs",
    });
  }

  function onNavigate(id) {
    setExpandNfType(null);
    navigate(ROUTES[id] ?? "/");
  }

  // While auth is enabled and the session is unresolved or absent, do not mount
  // the app: the redirect to Keycloak is already in flight (effect above).
  // Rendering pages here would fire API calls with no token and flash a 401
  // banner before the redirect lands. The callback and logged-out routes must
  // still render to drive their own flow, so they are exempt.
  const authPath = window.location.pathname;
  if (
    auth.enabled
    && authPath !== "/auth/callback"
    && authPath !== "/logged-out"
    && (auth.loading || !auth.user)
  ) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        Signing in…
      </div>
    );
  }

  return (
    <ErrorBoundary>
    <ToastProvider>
    <ConfirmProvider>
    <OperationsProvider>
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
        <Route path="/" element={<OverviewPage onNavigateToNf={handleNavigateToNf} />} />
        <Route path="/kubernetes" element={<KubernetesPage />} />
        <Route path="/core" element={
          <CorePage onOpenLogs={handleOpenLogs} onOpenTerminal={handleOpenTerminal} onOpenIperf3Logs={handleOpenIperf3Logs} expandNfType={expandNfType} />
        } />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/ran" element={<AdminOnly><RanPage /></AdminOnly>} />
        <Route path="/subscribers" element={<AdminOnly><SubscribersPage /></AdminOnly>} />
        <Route path="/ue-monitor" element={<UEMonitoringPage />} />
        <Route path="/diagnostics" element={<DiagnosticsPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
        <Route path="/services" element={<ServicesPage />} />
        <Route path="/services/northbound" element={<NorthboundPage />} />
        <Route path="/services/custom" element={<CustomWorkloadPage />} />
        <Route path="/services/apps" element={<AppsPage />} />
        <Route path="/northbound" element={<Navigate to="/services/northbound" replace />} />
        <Route path="/settings" element={<AdminOnly><SettingsPage /></AdminOnly>} />
        <Route path="/iam" element={<Navigate to="/settings" replace />} />
        <Route path="/branding" element={<Navigate to="/settings" replace />} />
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
    </OperationsProvider>
    </ConfirmProvider>
    </ToastProvider>
    </ErrorBoundary>
  );
}
