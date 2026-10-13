"""Web view (OTA-APP-002 7.1 and section 11 web tests): sign-in, CSRF, escaping, anchors, headers, expiry."""

import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from insights_testkit import ANCHOR, SCOPES, STORE, collector
from pydantic import SecretStr

from opstriage_insights.adapters.evidence_json import serialize
from opstriage_insights.adapters.fixtures import FixtureProblemSource
from opstriage_insights.adapters.guard import KeywordGuard
from opstriage_insights.adapters.llm_analyst import ScriptedAnalyst
from opstriage_insights.adapters.stores import MemoryReviewStore
from opstriage_insights.application.collect import BuildDigest, PrepareReview
from opstriage_insights.application.run_rca import PlanRca
from opstriage_insights.application.run_review import RunReview
from opstriage_insights.domain.findings import AnalystOutput, Finding, Recommendation
from opstriage_insights.domain.retention import RetentionPolicy
from opstriage_insights.entrypoints.api import Runner
from opstriage_insights.entrypoints.web import CSP, WebConfig, add_security_headers, web_router
from opstriage_insights.entrypoints.web_session import COOKIE, SessionManager
from opstriage_insights.entrypoints.web_view import hourly_bars, sparkline

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "opstriage_insights" / "templates"
OUTPUT = AnalystOutput(
    "Disk on aks-kafka-pool-0002 fills about 1% a day.",
    (
        Finding(
            "potential_issue",
            "Disk full in about 11 days <b>soon</b>",
            "Retention growth.",
            "high",
            "high",
            ("M1", "B1"),
            "about 11 days",
            "check per-topic size",
        ),
    ),
    (Recommendation("Review retention.ms on large topics", "frees disk before 90%", "low", ("M1",), (0,)),),
)


class Env:
    def __init__(self, *, allow_download: bool = False, proxy: bool = False) -> None:
        self.now = [ANCHOR + timedelta(minutes=5)]
        self.store = MemoryReviewStore()
        clock = lambda: self.now[0]  # noqa: E731
        prepare = PrepareReview(
            self.store,
            collector(),
            BuildDigest(KeywordGuard()),
            RetentionPolicy(),
            lambda: ANCHOR,
            lambda: UUID(int=11),
            serialize,
        )
        self.runner = Runner(
            RunReview(prepare, ScriptedAnalyst(OUTPUT), self.store, None, RetentionPolicy(), lambda: ANCHOR)
        )
        sessions = SessionManager(
            "s" * 32,
            timedelta(hours=8),
            clock=clock,
            proxy_header="X-Auth-Request-Email" if proxy else "",
            proxy_addresses=["testclient"] if proxy else [],
        )
        app = FastAPI()
        add_security_headers(app)
        app.include_router(
            web_router(
                dict(SCOPES),
                self.runner,
                self.store,
                sessions,
                WebConfig((SecretStr("good-token"),), allow_download),
                PlanRca(FixtureProblemSource(STORE), dict(SCOPES), lambda: ANCHOR),
                clock,
            )
        )
        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)

    def login(self) -> str:
        r = self.client.post("/ui/login", data={"token": "good-token", "next": "/ui/reports"})
        assert r.status_code == 303
        return csrf_of(self.client.get("/ui/reports").text)

    def review(self) -> str:
        csrf = self.login()
        r = self.client.post("/ui/reviews", data={"cluster": "planted-disk-trend", "csrf": csrf})
        assert r.status_code == 303
        self.client.portal.call(self.runner.drain)  # type: ignore[union-attr]
        return str(r.headers["location"])


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no csrf field"
    return m.group(1)


@pytest.fixture
def env() -> Any:
    e = Env()
    with e.client:
        yield e


def test_pages_require_sign_in_and_redirect_back(env: Env) -> None:
    r = env.client.get("/ui/reports?cluster=x")
    assert r.status_code == 303 and r.headers["location"] == "/ui/login?next=/ui/reports%3Fcluster%3Dx"
    page = env.client.get("/ui/login?next=https://evil.example/")
    assert 'name="next" value="/ui/reports"' in page.text  # open redirects are refused


def test_wrong_token_and_cookie_flags(env: Env) -> None:
    bad = env.client.post("/ui/login", data={"token": "nope", "next": "/ui/reports"})
    assert (
        bad.status_code == 401 and "not valid" in bad.text and COOKIE not in bad.headers.get("set-cookie", "")
    )
    good = env.client.post("/ui/login", data={"token": "good-token", "next": "/ui/reports"})
    cookie = good.headers["set-cookie"].lower()
    assert good.status_code == 303 and "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" in cookie and "path=/ui" in cookie  # testserver is not localhost, so Secure is set


def test_security_headers_on_every_ui_response(env: Env) -> None:
    for r in (
        env.client.get("/ui/login"),
        env.client.get("/ui/reports"),
        env.client.get("/ui/static/report.css"),
    ):
        assert r.headers["content-security-policy"] == CSP
        assert (
            r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
        )
    assert env.client.get("/ui/login").headers["cache-control"] == "no-store"
    assert "script-src" not in CSP and "default-src 'none'" in CSP


def test_csrf_is_required_for_every_form(env: Env) -> None:
    env.login()
    assert env.client.post("/ui/reviews", data={"cluster": "planted-disk-trend"}).status_code == 403
    assert (
        env.client.post("/ui/reviews", data={"cluster": "planted-disk-trend", "csrf": "x"}).status_code == 403
    )
    assert env.client.post("/ui/analyses", data={"display_id": "P-26101208", "csrf": "x"}).status_code == 403
    assert env.client.post("/ui/logout", data={"csrf": "x"}).status_code == 403


def test_review_list_report_page_and_citation_anchors(env: Env) -> None:
    location = env.review()
    html = env.client.get(location).text
    assert "Disk full in about 11 days" in html and "Review retention.ms on large topics" in html
    assert "Forecast (computed):" in html and "AI-generated" in html
    targets = set(re.findall(r'href="#(ev-[a-z0-9]+)"', html))
    ids = set(re.findall(r'id="(ev-[a-z0-9]+)"', html))
    assert targets and targets <= ids  # every citation link lands on an evidence entry
    assert '<svg class="spark"' in html and "<script" not in html
    listing = env.client.get("/ui/reports").text
    assert location in listing and "planted-disk-trend" in listing and "expires in 48 h" in listing


def test_untrusted_text_is_escaped(env: Env) -> None:
    location = env.review()
    rid = UUID(location.rsplit("/", 1)[1])
    report = env.store.reports[rid]["report"]
    log = (
        next(e for e in report["evidence"] if e["type"] == "log")
        if any(e["type"] == "log" for e in report["evidence"])
        else None
    )
    planted = {
        "id": "L9",
        "type": "log",
        "status": "ERROR",
        "count": 1,
        "recent_6h": 1,
        "first_seen": None,
        "last_seen": None,
        "logger": "x",
        "template": "<script>alert(1)</script>",
        "examples": ['"><img src=x onerror=alert(1)>'],
        "withheld": False,
        "sources": [],
        "hourly": [],
    }
    report["evidence"].append(planted if log is None else {**planted})
    report["summary"] = "<iframe src=//evil></iframe>"
    html = env.client.get(location).text
    for raw in ("<script>alert(1)</script>", "<img src=x onerror", "<iframe", "<b>soon</b>"):
        assert raw not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "&lt;b&gt;soon&lt;/b&gt;" in html


def test_expired_report_is_gone(env: Env) -> None:
    location = env.review()
    env.now[0] = ANCHOR + timedelta(hours=49)
    env.client.cookies.clear()
    env.login()  # the 8-hour session started at the old clock time; sign in again
    r = env.client.get(location)
    assert r.status_code == 410 and "has expired" in r.text and "Disk full" not in r.text
    assert env.client.get(f"/ui/reports/{UUID(int=12345)}").status_code == 404


def test_root_cause_form_from_a_card_link(env: Env) -> None:
    env.login()
    form = env.client.get("/ui/rca?display_id=P-26101208").text
    assert 'value="P-26101208"' in form
    assert 'value=""' in env.client.get('/ui/rca?display_id="><script>').text  # invalid ids are dropped
    csrf = csrf_of(form)
    missing = env.client.post("/ui/analyses", data={"display_id": "P-1", "csrf": csrf})
    assert missing.status_code == 404 and "unknown problem" in missing.text
    started = env.client.post("/ui/analyses", data={"display_id": "P-26101208", "csrf": csrf})
    assert started.status_code == 303 and started.headers["location"].startswith("/ui/reports/")
    env.client.portal.call(env.runner.drain)  # type: ignore[union-attr]
    page = env.client.get(started.headers["location"]).text
    assert "Root-cause analysis" in page and "P-26101208" in page


def test_running_page_refreshes(env: Env) -> None:
    import asyncio

    env.login()
    asyncio.run(
        env.store.start_review(UUID(int=77), "review", "c1", None, ANCHOR, ANCHOR + timedelta(hours=28))
    )
    html = env.client.get(f"/ui/reports/{UUID(int=77)}").text
    assert '<meta http-equiv="refresh" content="10">' in html and "Analysis running" in html


def test_markdown_download_is_off_by_default_and_attachment_when_on() -> None:
    off = Env()
    with off.client:
        location = off.review()
        assert off.client.get(f"{location}.md").status_code == 404
        assert "Download Markdown" not in off.client.get(location).text
    on = Env(allow_download=True)
    with on.client:
        location = on.review()
        r = on.client.get(f"{location}.md")
        assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment;")


def test_session_tampering_and_expiry() -> None:
    e = Env()
    with e.client:
        e.login()
        value = e.client.cookies.get(COOKIE)
        assert value
        e.client.cookies.set(COOKIE, value[:-2] + "xx", path="/ui")
        assert e.client.get("/ui/reports").status_code == 303
        e.client.cookies.clear()
        e.login()
        e.now[0] = e.now[0] + timedelta(hours=9)
        assert e.client.get("/ui/reports").status_code == 303


def test_proxy_identity_only_from_trusted_addresses() -> None:
    e = Env(proxy=True)
    with e.client:
        r = e.client.get("/ui/reports", headers={"X-Auth-Request-Email": "ops@example.com"})
        assert r.status_code == 200
        assert e.client.get("/ui/reports").status_code == 303  # no header, no session
    untrusted = Env()  # header configured off: the same header must not authenticate
    with untrusted.client:
        assert untrusted.client.get("/ui/reports", headers={"X-Auth-Request-Email": "x@y"}).status_code == 303


def test_templates_never_bypass_escaping() -> None:
    for t in TEMPLATES.glob("*.html"):
        text = t.read_text()
        assert not re.search(r"\|\s*safe\b", text), t.name
        assert "Markup(" not in text and "autoescape false" not in text, t.name
        assert "<script" not in text and "style=" not in text, t.name  # CSP: no scripts, no inline styles


def test_sparkline_and_bars_geometry() -> None:
    s = sparkline([1.0, 2.0, None, 4.0, 3.0])
    assert s is not None and s.path.startswith("M") and s.path.count("M") == 2  # the gap starts a new segment
    assert sparkline([None, 1.0]) is None
    bars = hourly_bars(
        [{"hour": "2026-10-12T01:00:00+00:00", "count": 2}, {"hour": "2026-10-12T02:00:00+00:00", "count": 4}]
    )
    assert len(bars) == 2 and bars[1].h > bars[0].h and bars[0].title == "01:00 UTC: 2"
