"""Every API route requires a role unless it is listed here.

main.py runs the check at startup and refuses to start on a route nobody decided
about: a router included without a role would otherwise be reachable by anyone
who reaches the backend. See docs/security/iam.md.
"""
from typing import Callable, Iterable

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route

# Reachable without a login, on purpose.
PUBLIC_ROUTES = {
    "GET /health",               # liveness (browser, watchdog)
    "GET /api/v1/cluster/info",  # mode and runtime source, read before login
    "GET /api/v1/apps/public",   # the front-door app list
}


def _calls(dependant) -> Iterable[Callable]:
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def unguarded_routes(app, guards: set, allowed: set) -> list[str]:
    """Routes with no dependency in `guards` whose "METHOD /path" is not in `allowed`."""
    found: list[str] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            names = [f"{method} {route.path}" for method in sorted(route.methods)]
        elif isinstance(route, APIWebSocketRoute):
            names = [f"WS {route.path}"]
        elif isinstance(route, Mount):
            # A mounted app has no dependencies to read: it must be listed.
            found += [name for name in [f"MOUNT {route.path}"] if name not in allowed]
            continue
        elif isinstance(route, Route):
            # A plain Starlette route has no dependencies either (APIRoute, a
            # subclass, is handled above). FastAPI's own docs pages are off.
            found += [name for name in (f"{m} {route.path}" for m in sorted(route.methods or [])) if name not in allowed]
            continue
        else:
            found.append(f"{type(route).__name__} {getattr(route, 'path', '?')}")
            continue
        if any(call in guards for call in _calls(route.dependant)):
            continue
        found += [name for name in names if name not in allowed]
    return found
