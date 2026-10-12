from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from opstriage_shared.asgi import BearerTokenMiddleware
from opstriage_shared.auth import bearer_guard


def _fastapi(tokens: list[str]) -> TestClient:
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(bearer_guard([SecretStr(t) for t in tokens]))])
    def x() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_guard_accepts_any_configured_token_for_rotation() -> None:
    client = _fastapi(["old", "new"])
    assert client.get("/x", headers={"Authorization": "Bearer old"}).status_code == 200
    assert client.get("/x", headers={"Authorization": "bearer new"}).status_code == 200


def test_guard_rejects_wrong_missing_or_non_bearer_tokens() -> None:
    client = _fastapi(["secret"])
    assert client.get("/x").status_code == 401
    assert client.get("/x", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/x", headers={"Authorization": "Basic secret"}).status_code == 401


def test_guard_fails_closed_without_tokens() -> None:
    assert _fastapi([]).get("/x", headers={"Authorization": "Bearer x"}).status_code == 503


def test_asgi_middleware_exempts_health_and_guards_everything_else() -> None:
    async def ok(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/healthz", ok), Route("/mcp", ok, methods=["POST"])])
    app.add_middleware(BearerTokenMiddleware, tokens=[SecretStr("t")], exempt_paths={"/healthz"})
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.post("/mcp").status_code == 401
    assert client.post("/mcp", headers={"Authorization": "Bearer t"}).status_code == 200
