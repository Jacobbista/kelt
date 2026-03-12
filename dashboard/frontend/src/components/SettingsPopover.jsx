import React, { useState, useEffect } from "react";

const STORAGE_KEY = "dashboard_admin_token";

export function useAdminToken() {
  const [token, setToken] = useState(() => localStorage.getItem(STORAGE_KEY) || "");

  useEffect(() => {
    if (token) {
      localStorage.setItem(STORAGE_KEY, token);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  }, [token]);

  return [token, setToken];
}

export default function SettingsPopover({ token, onTokenChange, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="w-96 rounded-lg border border-slate-700 bg-slate-900 p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white">Settings</h3>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white transition-colors"
          >
            &#x2715;
          </button>
        </div>

        <label className="block text-xs text-slate-400 mb-1.5">Admin Token</label>
        <input
          type="password"
          value={token}
          onChange={(e) => onTokenChange(e.target.value)}
          placeholder="Required for restart and write operations"
          className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:border-indigo-500 focus:outline-none"
        />
        <p className="mt-2 text-[10px] text-slate-500">
          Stored in browser localStorage. Required for deployment restarts and ConfigMap writes.
        </p>
      </div>
    </div>
  );
}
