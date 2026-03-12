import React, { useCallback, useEffect, useState } from "react";
import { getRuntimeInfo, restartBackend } from "./api";
import { OperationsProvider } from "./context/OperationsContext";
import { useBackendHealth } from "./hooks/useBackendHealth";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./Layout";
import LogViewer from "./components/LogViewer";
import CorePage from "./pages/CorePage";
import DiagnosticsPage from "./pages/DiagnosticsPage";
import MetricsPage from "./pages/MetricsPage";
import OverviewPage from "./pages/OverviewPage";
import RanPage from "./pages/RanPage";
import SubscribersPage from "./pages/SubscribersPage";
import TopologyPage from "./pages/TopologyPage";
import UEMonitoringPage from "./pages/UEMonitoringPage";

export default function App() {
  const [activePage, setActivePage] = useState("overview");
  const [runtime, setRuntime] = useState({ mode: "unknown", runtime_source: "unknown" });
  const [logTarget, setLogTarget] = useState(null);
  const [expandNfType, setExpandNfType] = useState(null);
  const { unreachable: backendUnreachable, check: checkBackend } = useBackendHealth();
  const [restartingBackend, setRestartingBackend] = useState(false);

  const handleRestartBackend = useCallback(async () => {
    setRestartingBackend(true);
    try {
      await restartBackend();
    } catch {
      /* Backend dies before responding — expected */
    }
    await new Promise((r) => setTimeout(r, 4000));
    await checkBackend();
    setRestartingBackend(false);
  }, [checkBackend]);

  useEffect(() => {
    getRuntimeInfo()
      .then((data) =>
        setRuntime({
          mode: (data.mode || "unknown").toLowerCase(),
          runtime_source: data.runtime_source || "unknown",
        })
      )
      .catch(() => {});
  }, []);

  function handleNavigateToNf(nfType) {
    setExpandNfType(nfType);
    setActivePage("core");
  }

  function handleOpenLogs(nf) {
    setLogTarget({ name: nf.name, containers: nf.containers, namespace: "5g" });
  }

  return (
    <ErrorBoundary>
    <OperationsProvider>
    <Layout
      activePage={activePage}
      onNavigate={(page) => { setActivePage(page); setExpandNfType(null); }}
      runtime={runtime}
      backendUnreachable={backendUnreachable}
      onRestartBackend={handleRestartBackend}
      restartingBackend={restartingBackend}
    >
      {activePage === "overview" && (
        <OverviewPage onNavigateToNf={handleNavigateToNf} />
      )}
      {activePage === "core" && (
        <CorePage onOpenLogs={handleOpenLogs} expandNfType={expandNfType} />
      )}
      {activePage === "topology" && <TopologyPage />}
      {activePage === "ran" && <RanPage />}
      {activePage === "subscribers" && <SubscribersPage />}
      {activePage === "ue-monitoring" && <UEMonitoringPage />}
      {activePage === "diagnostics" && <DiagnosticsPage />}
      {activePage === "metrics" && <MetricsPage />}

      {logTarget && (
        <LogViewer
          namespace={logTarget.namespace}
          pod={logTarget.name}
          container={logTarget.containers?.[0]}
          onClose={() => setLogTarget(null)}
        />
      )}
    </Layout>
    </OperationsProvider>
    </ErrorBoundary>
  );
}
