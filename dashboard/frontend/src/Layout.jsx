import React from "react";
import Sidebar from "./components/Sidebar";
import AppHeader from "./components/AppHeader";

export default function Layout({ onNavigate, runtime, children, serverTime }) {
  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      <Sidebar onNavigate={onNavigate} />
      <div className="ml-56 flex min-h-screen min-w-0 flex-1 flex-col">
        <AppHeader runtime={runtime} serverTime={serverTime} />
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
