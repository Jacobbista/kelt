import React from "react";

/**
 * Catches React render errors and shows a fallback with Reload.
 * Prevents full app crash when a child component throws.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("[ErrorBoundary]", error, info);
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen flex-col items-center justify-center bg-slate-950 p-6 text-slate-200">
          <div className="max-w-md rounded-lg border border-rose-700/50 bg-slate-900 p-6 text-center">
            <h2 className="mb-2 text-lg font-semibold text-rose-400">Something went wrong</h2>
            <p className="mb-4 text-sm text-slate-400">
              The app encountered an error. Reload the page to recover.
            </p>
            <button
              type="button"
              onClick={this.handleReload}
              className="rounded bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500"
            >
              Reload page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
