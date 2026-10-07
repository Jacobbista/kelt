import React, { useEffect, useRef, useState } from "react";
import { Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom";
import { getRuntimeInfo } from "./api";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ConfirmProvider } from "./context/ConfirmContext";
import { UpdateProvider } from "./context/UpdateContext";
import { IsolationSummaryProvider } from "./context/IsolationSummaryContext";
import { StatusSummaryProvider } from "./context/StatusSummaryContext";
import { useBackendHealth } from "./hooks/useBackendHealth";
import { useIamHealth } from "./hooks/useIamHealth";
import { useWatchdog } from "./hooks/useWatchdog";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./Layout";
import { ROUTES } from "./navigation";
import LogViewer from "./components/LogViewer";
import PodTerminal from "./components/PodTerminal";
import AuthGate from "./components/AuthGate";
import CorePage from "./pages/CorePage";
import HealthPage from "./pages/HealthPage";
import CapturePage from "./pages/CapturePage";
import IsolationPage from "./pages/IsolationPage";
import SettingsPage from "./pages/SettingsPage";
import IamPage from "./pages/IamPage";
import BrandingPage from "./pages/BrandingPage";
import StoragePage from "./pages/StoragePage";
import KubernetesPage from "./pages/KubernetesPage";
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
import AuditPage from "./pages/AuditPage";
import RanPage from "./pages/RanPage";
import SubscribersPage from "./pages/SubscribersPage";
import TopologyPage from "./pages/TopologyPage";
import UEMonitoringPage from "./pages/UEMonitoringPage";
import { gateView } from "./lib/authFlow";


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
  const { pathname } = useLocation();
  const auth = useAuth();
  // Once the shell has opened it stays mounted: a lost backend covers it
  // (gate "backend-lost") instead of unmounting the pages.
  const [appShown, setAppShown] = useState(false);
  const [runtime, setRuntime] = useState({ mode: "unknown", runtime_source: "unknown" });
  const [logTarget, setLogTarget] = useState(null);
  const [termTarget, setTermTarget] = useState(null);
  const { state: backendState, serverTime, check: recheckBackend } = useBackendHealth();
  const iamState = useIamHealth();
  const watchdog = useWatchdog(auth);
  const isAdmin = !auth.enabled || auth.roles.includes("dashboard-admin");
  const view = gateView({ ...auth, path: pathname, backend: backendState, iam: iamState, appShown });
  useEffect(() => {
    if (view === "app") setAppShown(true);
  }, [view]);
  // The cover over the shell fades out (index.css .gate.is-leaving) when the
  // backend answers again, instead of vanishing in one frame.
  const [coverLeaving, setCoverLeaving] = useState(false);
  const lastView = useRef(view);
  useEffect(() => {
    if (lastView.current === "backend-lost" && view === "app") {
      setCoverLeaving(true);
      const id = setTimeout(() => setCoverLeaving(false), 250);
      lastView.current = view;
      return () => clearTimeout(id);
    }
    lastView.current = view;
    return undefined;
  }, [view]);

  // A first visit with no session goes to Keycloak by itself (gate
  // "redirecting"). Once per page load: StrictMode runs effects twice, and two
  // sign-in redirects at once leave Keycloak with a stale auth session.
  const redirected = useRef(false);
  useEffect(() => {
    if (view !== "redirecting" || redirected.current) return;
    redirected.current = true;
    auth.login();
  }, [view, auth.login]);

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

  // No session, no backend or Keycloak yet, or an auth route: the gate's full
  // screen, never the shell (lib/authFlow.js gateView). Pages mount only with a
  // session, so they never fire API calls without a token.
  const gate = <AuthGate view={view} auth={auth} backend={backendState} iam={iamState} watchdog={watchdog} isAdmin={isAdmin} recheck={recheckBackend} />;
  if (view !== "app" && view !== "backend-lost") return gate;

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
    >
      <Routes>
        <Route path="/logged-out" element={<Navigate to="/" replace />} />
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
        <Route path="/settings/audit" element={<AdminOnly><AuditPage /></AdminOnly>} />
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
    {(view === "backend-lost" || coverLeaving) && (
      <AuthGate view="backend-lost" leaving={coverLeaving} auth={auth} watchdog={watchdog} isAdmin={isAdmin} recheck={recheckBackend} />
    )}
    </StatusSummaryProvider>
    </IsolationSummaryProvider>
    </UpdateProvider>
    </ConfirmProvider>
    </ToastProvider>
    </ErrorBoundary>
  );
}
