import { useAuth } from "../auth/AuthContext";

export default function LoggedOutPage() {
  const auth = useAuth();
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-200">
      <div className="max-w-md rounded-lg border border-slate-800 bg-slate-900/60 p-6 text-center">
        <h2 className="mb-2 text-lg font-semibold text-slate-100">Signed out</h2>
        <p className="mb-4 text-sm text-slate-400">
          Dashboard session ended. The browser may still hold a single-sign-on
          session at the identity provider; signing in again will reuse it
          unless that session is also terminated.
        </p>
        <button
          type="button"
          onClick={() => auth.login()}
          className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
        >
          Sign in again
        </button>
      </div>
    </div>
  );
}
