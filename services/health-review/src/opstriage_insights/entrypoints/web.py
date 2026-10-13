"""Server-rendered web view (OTA-APP-002 7.1): report list, report page, root-cause form, sign-in.

No JavaScript; every page is rendered by Jinja2 with autoescape on and no |safe filters. Log-derived text is
untrusted and is only ever rendered as escaped text. Pages are `no-store`; a strict CSP is set by the app.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import SecretStr

from opstriage_insights.application.run_rca import PlanRca, RcaError
from opstriage_insights.domain.model import ClusterScope
from opstriage_insights.entrypoints.api import Runner
from opstriage_insights.entrypoints.web_session import COOKIE, Session, SessionManager
from opstriage_insights.entrypoints.web_view import expires_in, fmt_num, report_view
from opstriage_shared.auth import token_matches

DISPLAY_ID = re.compile(r"^P-[0-9]{1,20}$")
NO_STORE = {"Cache-Control": "no-store"}
CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

templates = Environment(
    loader=PackageLoader("opstriage_insights", "templates"),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
templates.filters["num"] = lambda v: "-" if v is None else fmt_num(float(v))


@dataclass(frozen=True)
class WebConfig:
    service_tokens: tuple[SecretStr, ...]
    allow_download: bool = False


def _safe_next(target: str | None) -> str:
    """Only same-site paths under /ui (no scheme, no //host) are allowed as a post-login redirect."""
    if target and target.startswith("/ui") and not target.startswith("//") and "\\" not in target:
        return target
    return "/ui/reports"


def _is_local(request: Request) -> bool:
    return (request.url.hostname or "") in {"localhost", "127.0.0.1", "::1"}


def page(name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    return HTMLResponse(templates.get_template(name).render(**context), status_code, headers=NO_STORE)


def web_router(
    clusters: dict[str, ClusterScope],
    runner: Runner,
    store: Any,
    sessions: SessionManager,
    config: WebConfig,
    plan_rca: PlanRca | None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/ui")
    accepted = [t.get_secret_value().encode() for t in config.service_tokens]

    def current(request: Request) -> Session:
        proxied = sessions.from_proxy(
            {k.lower(): v for k, v in request.headers.items()},
            request.client.host if request.client else None,
        )
        session = proxied or sessions.read(request.cookies.get(COOKIE))
        if session is None:
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            raise HTTPException(
                status.HTTP_303_SEE_OTHER, headers={"Location": f"/ui/login?next={_quote(target)}"}
            )
        return session

    def require_csrf(session: Session, csrf: str | None) -> None:
        if not sessions.csrf_ok(session, csrf):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "the form expired; reload the page and try again")

    # ---- sign-in ------------------------------------------------------------------------------------------
    @router.get("/login", response_class=HTMLResponse)
    async def login_form(next: str | None = None) -> HTMLResponse:
        return page("login.html", next=_safe_next(next), error=None)

    @router.post("/login")
    async def login(
        request: Request, token: Annotated[str, Form()], next: Annotated[str, Form()] = ""
    ) -> Response:
        if not accepted or not token_matches(f"Bearer {token}", accepted):
            return page("login.html", 401, next=_safe_next(next), error="That access token is not valid.")
        value, _session = sessions.issue("token-user")
        response = RedirectResponse(_safe_next(next), status.HTTP_303_SEE_OTHER, headers=NO_STORE)
        response.set_cookie(
            COOKIE,
            value,
            max_age=int(sessions.ttl.total_seconds()),
            httponly=True,
            samesite="strict",
            secure=not _is_local(request),
            path="/ui",
        )
        return response

    @router.post("/logout")
    async def logout(
        user: Annotated[Session, Depends(current)], csrf: Annotated[str, Form()] = ""
    ) -> Response:
        require_csrf(user, csrf)
        response = RedirectResponse("/ui/login", status.HTTP_303_SEE_OTHER, headers=NO_STORE)
        response.delete_cookie(COOKIE, path="/ui")
        return response

    # ---- reports -------------------------------------------------------------------------------------------
    @router.get("", include_in_schema=False)
    @router.get("/", include_in_schema=False)
    async def home() -> Response:
        return RedirectResponse("/ui/reports", status.HTTP_303_SEE_OTHER)

    @router.get("/reports", response_class=HTMLResponse)
    async def report_list(
        user: Annotated[Session, Depends(current)], cluster: str | None = None, kind: str | None = None
    ) -> HTMLResponse:
        now = clock()
        rows = await store.list_reports(now, cluster or None)
        if kind in ("review", "rca"):
            rows = [r for r in rows if r.kind == kind]
        items = [{"row": r, "expires": expires_in(r.expires_at, now)} for r in rows]
        return page(
            "report_list.html",
            user=user,
            items=items,
            clusters=sorted(clusters),
            cluster=cluster or "",
            kind=kind or "",
            now=now,
        )

    @router.get("/reports/{review_id}.md")
    async def report_markdown(user: Annotated[Session, Depends(current)], review_id: UUID) -> Response:
        if not config.allow_download:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "downloads are disabled")
        stored = await store.get_report(review_id, clock())
        if stored is None or stored.expired or stored.markdown is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no report")
        return Response(
            stored.markdown,
            media_type="text/markdown; charset=utf-8",
            headers={
                **NO_STORE,
                "Content-Disposition": f'attachment; filename="health-review-{review_id}.md"',
            },
        )

    @router.get("/reports/{review_id}", response_class=HTMLResponse)
    async def report_page(user: Annotated[Session, Depends(current)], review_id: UUID) -> HTMLResponse:
        now = clock()
        stored = await store.get_report(review_id, now)
        if stored is None:
            return page(
                "message.html",
                404,
                user=user,
                title="Report not found",
                text="There is no report with this id. It may never have existed.",
            )
        if stored.expired:
            return page(
                "message.html",
                410,
                user=user,
                title="This report has expired",
                text=f"It expired at {stored.expires_at:%Y-%m-%d %H:%M} UTC and its data was deleted.",
            )
        if stored.report is None:
            row = await store.get_review(review_id)
            if row is not None and row.status == "failed":
                return page(
                    "message.html",
                    200,
                    user=user,
                    title="This review failed",
                    text=f"The review could not complete ({row.error or 'unknown error'}).",
                )
            return page("running.html", 200, user=user, review_id=review_id, row=row)
        return page(
            "report.html", user=user, v=report_view(stored.report, now), allow_download=config.allow_download
        )

    # ---- start a review or a root-cause analysis ---------------------------------------------------------
    @router.post("/reviews")
    async def start_review(
        user: Annotated[Session, Depends(current)],
        cluster: Annotated[str, Form()],
        csrf: Annotated[str, Form()] = "",
    ) -> Response:
        require_csrf(user, csrf)
        scope = clusters.get(cluster)
        if scope is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown cluster")
        review_id = runner.start(scope)
        return RedirectResponse(f"/ui/reports/{review_id}", status.HTTP_303_SEE_OTHER, headers=NO_STORE)

    @router.get("/rca", response_class=HTMLResponse)
    async def rca_form(user: Annotated[Session, Depends(current)], display_id: str = "") -> HTMLResponse:
        valid = bool(DISPLAY_ID.match(display_id))
        return page(
            "rca.html",
            user=user,
            display_id=display_id if valid else "",
            error=None,
            clusters=sorted(clusters),
            cluster="",
        )

    @router.post("/analyses")
    async def start_rca(
        user: Annotated[Session, Depends(current)],
        display_id: Annotated[str, Form()],
        csrf: Annotated[str, Form()] = "",
        cluster: Annotated[str, Form()] = "",
    ) -> Response:
        require_csrf(user, csrf)
        args = {"user": user, "display_id": display_id, "clusters": sorted(clusters), "cluster": cluster}
        if not DISPLAY_ID.match(display_id):
            return page("rca.html", 422, error="A problem id looks like P-26101208.", **args)
        if plan_rca is None:
            return page("rca.html", 503, error="Root-cause analysis is not configured.", **args)
        try:
            plan = await plan_rca(display_id, cluster or None)
        except RcaError as exc:
            return page("rca.html", exc.status, error=exc.detail, **args)
        review_id = runner.start(plan.scope, plan.subject, plan.window)
        return RedirectResponse(f"/ui/reports/{review_id}", status.HTTP_303_SEE_OTHER, headers=NO_STORE)

    # ---- stylesheet (CSP style-src 'self': no inline styles anywhere) --------------------------------------
    @router.get("/static/report.css", include_in_schema=False)
    async def stylesheet() -> Response:
        css = files("opstriage_insights").joinpath("static/report.css").read_text(encoding="utf-8")
        return Response(css, media_type="text/css; charset=utf-8", headers={"Cache-Control": "max-age=300"})

    return router


def add_security_headers(app: FastAPI) -> None:
    """CSP and friends on every /ui response (pages, redirects and the stylesheet)."""

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/ui"):
            for name, value in SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
        return response


def _quote(path: str) -> str:
    from urllib.parse import quote

    return quote(path, safe="/")
