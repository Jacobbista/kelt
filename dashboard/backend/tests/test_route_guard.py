import unittest

from fastapi import APIRouter, Depends, FastAPI

from app.route_guard import unguarded_routes


def guard():
    return True


def build_app() -> FastAPI:
    # As in app/main.py: FastAPI's own docs pages off.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    guarded = APIRouter(prefix="/api/v1/guarded")
    guarded.get("/x")(lambda: 1)
    unguarded = APIRouter(prefix="/api/v1/open")
    unguarded.get("/y")(lambda: 1)
    inline = APIRouter(prefix="/api/v1/inline")

    @inline.post("/z")
    def z(_=Depends(guard)):
        return 1

    ws = APIRouter(prefix="/api/v1/ws")

    @ws.websocket("/stream")
    async def stream(websocket):
        return None

    app.include_router(guarded, dependencies=[Depends(guard)])
    app.include_router(unguarded)
    app.include_router(inline)
    app.include_router(ws)
    app.get("/health")(lambda: 1)
    return app


class RouteGuardTest(unittest.TestCase):
    def test_reports_unguarded_routes_that_are_not_allowed(self):
        found = unguarded_routes(build_app(), {guard}, {"GET /health"})
        self.assertEqual(found, ["GET /api/v1/open/y", "WS /api/v1/ws/stream"])

    def test_framework_docs_pages_are_reported_when_turned_on(self):
        app = FastAPI()
        found = unguarded_routes(app, {guard}, set())
        self.assertIn("GET /docs", found)
        self.assertIn("GET /openapi.json", found)

    def test_allowed_routes_are_not_reported(self):
        allowed = {"GET /health", "GET /api/v1/open/y", "WS /api/v1/ws/stream"}
        self.assertEqual(unguarded_routes(build_app(), {guard}, allowed), [])

    def test_a_guard_on_the_endpoint_itself_counts(self):
        found = unguarded_routes(build_app(), {guard}, {"GET /health"})
        self.assertNotIn("POST /api/v1/inline/z", found)

    def test_a_mount_is_reported(self):
        from starlette.staticfiles import StaticFiles
        app = build_app()
        app.mount("/static", StaticFiles(directory=".", check_dir=False), name="static")
        allowed = {"GET /health", "GET /api/v1/open/y", "WS /api/v1/ws/stream"}
        self.assertEqual(unguarded_routes(app, {guard}, allowed), ["MOUNT /static"])

    def test_a_plain_route_is_reported(self):
        app = build_app()
        app.add_route("/raw", lambda request: None, methods=["GET"])
        self.assertIn("GET /raw", unguarded_routes(app, {guard}, {"GET /health"}))

    def test_an_allowed_mount_is_not_reported(self):
        from starlette.staticfiles import StaticFiles
        app = build_app()
        app.mount("/static", StaticFiles(directory=".", check_dir=False), name="static")
        allowed = {"GET /health", "GET /api/v1/open/y", "WS /api/v1/ws/stream", "MOUNT /static"}
        self.assertEqual(unguarded_routes(app, {guard}, allowed), [])


if __name__ == "__main__":
    unittest.main()
