import React, { useEffect, useState } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { getRuntimeInfo } from "./api";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { OperationsProvider } from "./context/OperationsContext";
import { useBackendHealth } from "./hooks/useBackendHealth";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./Layout";
import LogViewer from "./components/LogViewer";
import PodTerminal from "./components/PodTerminal";
import CallbackPage from "./pages/CallbackPage";
import CorePage from "./pages/CorePage";
import DiagnosticsPage from "./pages/DiagnosticsPage";
import KubernetesPage from "./pages/KubernetesPage";
import MetricsPage from "./pages/MetricsPage";
import OverviewPage from "./pages/OverviewPage";
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
};

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}

function AppInner() {
  const navigate = useNavigate();
  const auth = useAuth();
  const [runtime, setRuntime] = useState({ mode: "unknown", runtime_source: "unknown" });
  const [logTarget, setLogTarget] = useState(null);
  const [termTarget, setTermTarget] = useState(null);
  const [expandNfType, setExpandNfType] = useState(null);
  const { unreachable: backendUnreachable, serverTime } = useBackendHealth();

  useEffect(() => {
    // When auth is enabled but the user is not signed in, redirect to
    // Keycloak immediately. The auth/callback route handles the return
    // trip; everything else requires a token.
    if (auth.enabled && !auth.loading && !auth.user && window.location.pathname !== "/auth/callback") {
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

  return (
    <ErrorBoundary>
    <OperationsProvider>
    <Layout
      onNavigate={onNavigate}
      runtime={runtime}
      serverTime={serverTime}
      backendUnreachable={backendUnreachable}
    >
      <Routes>
        <Route path="/auth/callback" element={<CallbackPage />} />
        <Route path="/" element={<OverviewPage onNavigateToNf={handleNavigateToNf} />} />
        <Route path="/kubernetes" element={<KubernetesPage />} />
        <Route path="/core" element={
          <CorePage onOpenLogs={handleOpenLogs} onOpenTerminal={handleOpenTerminal} onOpenIperf3Logs={handleOpenIperf3Logs} expandNfType={expandNfType} />
        } />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/ran" element={<RanPage />} />
        <Route path="/subscribers" element={<SubscribersPage />} />
        <Route path="/ue-monitor" element={<UEMonitoringPage />} />
        <Route path="/diagnostics" element={<DiagnosticsPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
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
    </ErrorBoundary>
  );
}
