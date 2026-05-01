import React, { useEffect, useState, useCallback } from "react";
import { getRuntimeInfo } from "./api";
import { OperationsProvider } from "./context/OperationsContext";
import { useBackendHealth } from "./hooks/useBackendHealth";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./Layout";
import LogViewer from "./components/LogViewer";
import PodTerminal from "./components/PodTerminal";
import CorePage from "./pages/CorePage";
import DiagnosticsPage from "./pages/DiagnosticsPage";
import KubernetesPage from "./pages/KubernetesPage";
import MetricsPage from "./pages/MetricsPage";
import OverviewPage from "./pages/OverviewPage";
import RanPage from "./pages/RanPage";
import SubscribersPage from "./pages/SubscribersPage";
import TopologyPage from "./pages/TopologyPage";
import UEMonitoringPage from "./pages/UEMonitoringPage";

export default function App() {
  const [activePage, setActivePage] = useState("overview");
  const [runtime, setRuntime] = useState({ mode: "unknown", runtime_source: "unknown", open5gs_webui_url: "" });
  const [logTarget, setLogTarget] = useState(null);
  const [termTarget, setTermTarget] = useState(null);
  const [expandNfType, setExpandNfType] = useState(null);
  const { unreachable: backendUnreachable, serverTime } = useBackendHealth();

  useEffect(() => {
    getRuntimeInfo()
      .then((data) =>
        setRuntime({
          mode: (data.mode || "unknown").toLowerCase(),
          runtime_source: data.runtime_source || "unknown",
          open5gs_webui_url: data.open5gs_webui_url || "",
        })
      )
      .catch(() => {});
  }, []);

  function handleNavigateToNf(nfType) {
    setExpandNfType(nfType);
    setActivePage("core");
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

  return (
    <ErrorBoundary>
    <OperationsProvider>
    <Layout
      activePage={activePage}
      onNavigate={(page) => { setActivePage(page); setExpandNfType(null); }}
      runtime={runtime}
      serverTime={serverTime}
      backendUnreachable={backendUnreachable}
    >
      {activePage === "overview" && (
        <OverviewPage onNavigateToNf={handleNavigateToNf} />
      )}
      {activePage === "kubernetes" && <KubernetesPage />}
      {activePage === "core" && (
        <CorePage onOpenLogs={handleOpenLogs} onOpenTerminal={handleOpenTerminal} onOpenIperf3Logs={handleOpenIperf3Logs} expandNfType={expandNfType} />
      )}
      {activePage === "topology" && <TopologyPage />}
      {activePage === "ran" && <RanPage />}
      {activePage === "subscribers" && <SubscribersPage open5gsWebuiUrl={runtime.open5gs_webui_url} />}
      {activePage === "ue-monitoring" && <UEMonitoringPage />}
      {activePage === "diagnostics" && <DiagnosticsPage />}
      {activePage === "metrics" && <MetricsPage />}

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
