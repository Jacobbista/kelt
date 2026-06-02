import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import SystemHealthGate from "./components/SystemHealthGate";
import "./index.css";
import "@xyflow/react/dist/style.css";
import "@xterm/xterm/css/xterm.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <SystemHealthGate>
        <App />
      </SystemHealthGate>
    </BrowserRouter>
  </React.StrictMode>
);
