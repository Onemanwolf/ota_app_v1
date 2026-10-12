"""Generate the Postman collection from the fixture files, so request bodies always match the fixtures.

Run: python3 postman/build_collection.py   (stdlib only; rewrites OpsTriage-local.postman_collection.json)

Bodies are the fixture JSON verbatim except `event_id` / `display_id` (classic: `pid` / `problem_id`), which
become {{variables}} that pre-request scripts randomise, so the collection can be re-run without every
request turning into a duplicate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "dynatrace"
OUT = Path(__file__).resolve().parent / "OpsTriage-local.postman_collection.json"

# ------------------------------------------------------------------------------------------- script snippets
NEW_IDS = """const n = () => Math.floor(Math.random() * 9e17 + 1e17).toString();
pm.collectionVariables.set('event_id', '-' + n() + '_1760300000000V2');
pm.collectionVariables.set('display_id', 'P-PM' + Math.floor(Math.random() * 9e6 + 1e6));"""

KEV_DECIDED = """pm.test('200', () => pm.response.to.have.status(200));
const run = pm.response.json();
pm.test('decided by the model', () => pm.expect(run.decision.source).to.eql('model'));
pm.test('kev-latest', () => pm.expect(run.decision.model).to.eql('kev-latest'));
pm.test('all six action probabilities', () => pm.expect(Object.keys(run.decision.action_probabilities)).to.have.lengthOf(6));
pm.test('no decision-model error', () => pm.expect(run.decision_error).to.be.oneOf([null, undefined]));
if (run.plan.includes('remediate')) {
  pm.test('remediation only after a restart decision', () => pm.expect(run.decision.action).to.eql('restart_container'));
}"""

NEW_CLASSIC_IDS = """const n = () => Math.floor(Math.random() * 9e17 + 1e17).toString();
pm.collectionVariables.set('classic_pid', '-' + n() + '_1760263200000V2');
pm.collectionVariables.set('classic_display_id', 'P-PMC' + Math.floor(Math.random() * 9e6 + 1e6));"""


def save_alert(var: str = "alert_id", status: int = 202) -> str:
    return f"""pm.test('accepted ({status})', () => pm.response.to.have.status({status}));
const body = pm.response.json();
pm.test('not a duplicate', () => pm.expect(body.duplicate).to.eql(false));
pm.collectionVariables.set('{var}', body.alert_id);"""


def wait_for(var: str, states: tuple[str, ...] = ("done",), tries: int = 40) -> tuple[str, str]:
    """Polls GET /v1/alerts/{id} by re-queueing itself (works in the Collection Runner and newman)."""
    pre = "setTimeout(() => {}, 400);"
    test = f"""const s = pm.response.json().state;
const tries = Number(pm.collectionVariables.get('_tries') || 0);
const wanted = {json.dumps(list(states))};
if (!wanted.includes(s) && s !== 'failed' && tries < {tries}) {{
  pm.collectionVariables.set('_tries', tries + 1);
  postman.setNextRequest(pm.info.requestName);
}} else {{
  pm.collectionVariables.set('_tries', 0);
  pm.test('alert reached ' + wanted.join(' or ') + ' (was ' + s + ')', () => pm.expect(wanted).to.include(s));
}}"""
    return pre, test


def check_plan(expected: list[str]) -> str:
    return f"""pm.test('200', () => pm.response.to.have.status(200));
const run = pm.response.json();
const expected = {json.dumps(expected)};
pm.test('plan is ' + JSON.stringify(expected), () => pm.expect(run.plan).to.eql(expected));"""


# ------------------------------------------------------------------------------------------- builders
def fixture_body(kind: str, name: str, *, ids: tuple[str, str] | None = None) -> str:
    data: dict[str, Any] = json.loads((FIXTURES / kind / f"{name}.json").read_text())
    if kind == "workflow":
        id_key, display_key = "event_id", "display_id"
        id_var, display_var = ids or ("event_id", "display_id")
    else:
        id_key, display_key = "pid", "problem_id"
        id_var, display_var = ids or ("classic_pid", "classic_display_id")
    if id_key in data:
        data[id_key] = f"{{{{{id_var}}}}}"
        data[display_key] = f"{{{{{display_var}}}}}"
        if "details" in data:  # classic: keep ProblemDetailsJSONv2 consistent
            data["details"]["problemId"] = f"{{{{{id_var}}}}}"
            data["details"]["displayId"] = f"{{{{{display_var}}}}}"
        if data.get("problem_url"):
            data["problem_url"] = data["problem_url"].rsplit("/", 1)[0] + f"/{{{{{display_var}}}}}"
    return json.dumps(data, indent=2)


def url_object(url: str) -> dict[str, Any]:
    """The parsed form Postman exports ({raw, host, path, query}); the app's editor expects it."""
    base, _, query = url.partition("?")
    if base.startswith("{{"):
        host, _, path = base.partition("}}")
        host += "}}"
    else:
        host, path = base, ""
    out: dict[str, Any] = {"raw": url, "host": [host], "path": [s for s in path.split("/") if s]}
    if query:
        out["query"] = [{"key": k, "value": v} for k, _, v in (q.partition("=") for q in query.split("&"))]
    return out


def request(
    name: str,
    method: str,
    url: str,
    *,
    auth: str | None = None,
    body: str | None = None,
    pre: str | None = None,
    test: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    headers = [{"key": "Content-Type", "value": "application/json"}] if body is not None else []
    if auth:
        headers.append({"key": "Authorization", "value": f"Bearer {{{{{auth}}}}}"})
    item: dict[str, Any] = {
        "name": name,
        "request": {"method": method, "header": headers, "url": url_object(url), "description": description},
        "event": [],
    }
    if body is not None:
        item["request"]["body"] = {"mode": "raw", "raw": body, "options": {"raw": {"language": "json"}}}
    if pre:
        item["event"].append(
            {"listen": "prerequest", "script": {"type": "text/javascript", "exec": pre.split("\n")}}
        )
    if test:
        item["event"].append(
            {"listen": "test", "script": {"type": "text/javascript", "exec": test.split("\n")}}
        )
    return item


def folder(name: str, items: list[dict[str, Any]], description: str = "") -> dict[str, Any]:
    return {"name": name, "description": description, "item": items}


# Defaults for every variable. The environment file overrides them; they make the collection work even when no
# environment is selected in the app (otherwise {{intake}} stays literal and every request is a "network error").
DEFAULTS = {
    "intake": "http://localhost:8000",
    "agent": "http://localhost:8010",
    "mcp_notification": "http://localhost:8101",
    "mcp_ticketing": "http://localhost:8102",
    "mcp_remediation": "http://localhost:8103",
    "sink": "http://localhost:8091",
    "mailpit": "http://localhost:8025",
    # Kev through the Mac-local tunnel (scripts/kev-tunnel.sh): no macOS Local Network permission needed.
    "kev": "http://127.0.0.1:18003",
    "webhook_token": "dev-webhook-token",
    "service_token": "dev-poller-token",
    "admin_token": "dev-admin-token",
    "wrong_token": "not-a-valid-token",
}

COLLECTION_VERSION = 3  # bump when the structure changes, so the app shows which copy is imported

# Runs before every request: fail with a readable message instead of "getaddrinfo ENOTFOUND {{intake}}".
VARIABLE_GUARD = """const missing = ['intake', 'agent', 'sink', 'mailpit', 'mcp_ticketing'].filter(k => !pm.variables.get(k));
if (missing.length) {
  throw new Error('Variables not set: ' + missing.join(', ') + '. Re-import OpsTriage-local.postman_collection.json '
    + '(choose Replace) or select the opstriage-local environment.');
}"""

WF = "{{intake}}/v1/webhooks/dynatrace/workflow"
CL = "{{intake}}/v1/webhooks/dynatrace/classic"


def post_wait_run(label: str, fixture: str, expected_plan: list[str]) -> list[dict[str, Any]]:
    var = f"alert_{fixture}"
    pre, test = wait_for(var)
    return [
        request(
            f"{label}: POST {fixture}",
            "POST",
            WF,
            auth="webhook_token",
            body=fixture_body("workflow", fixture),
            pre=NEW_IDS,
            test=save_alert(var),
        ),
        request(
            f"{label}: wait for done",
            "GET",
            f"{{{{intake}}}}/v1/alerts/{{{{{var}}}}}",
            auth="service_token",
            pre=pre,
            test=test,
        ),
        request(
            f"{label}: run plan",
            "GET",
            f"{{{{agent}}}}/v1/runs/{{{{{var}}}}}",
            auth="admin_token",
            test=check_plan(expected_plan),
        ),
    ]


def build() -> dict[str, Any]:
    wait_pre, wait_test = wait_for("alert_id")
    health = folder(
        "0 Health",
        [
            request(
                f"{svc} healthz",
                "GET",
                f"{{{{{var}}}}}/healthz",
                test="pm.test('200', () => pm.response.to.have.status(200));",
            )
            for svc, var in [
                ("intake-api", "intake"),
                ("agent-runtime", "agent"),
                ("mcp-notification", "mcp_notification"),
                ("mcp-ticketing", "mcp_ticketing"),
                ("mcp-remediation", "mcp_remediation"),
                ("webhook-sink (mock Teams)", "sink"),
            ]
        ]
        + [
            request(
                "intake-api readyz",
                "GET",
                "{{intake}}/readyz",
                test="pm.test('ready', () => pm.response.to.have.status(200));",
            ),
            request(
                "agent-runtime readyz",
                "GET",
                "{{agent}}/readyz",
                test="pm.test('ready', () => pm.response.to.have.status(200));",
            ),
            request(
                "Mailpit (mock email) info",
                "GET",
                "{{mailpit}}/api/v1/info",
                test="pm.test('200', () => pm.response.to.have.status(200));",
            ),
            request(
                "Kev on the Spark: models",
                "GET",
                "{{kev}}/v1/models",
                test="pm.test('kev-latest is served', () => pm.expect(pm.response.json().models.map(m => m.name)).to.include('kev-latest'));",
            ),
        ],
        "Every service is up. The Kev check needs the Spark reachable.",
    )

    happy = folder(
        "1 Workflow webhook happy path",
        [
            request("Clear mock Teams", "DELETE", "{{sink}}/requests"),
            request(
                "POST container_down_active",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "container_down_active"),
                pre=NEW_IDS,
                test=save_alert(),
            ),
            request(
                "Wait for alert done",
                "GET",
                "{{intake}}/v1/alerts/{{alert_id}}",
                auth="service_token",
                pre=wait_pre,
                test=wait_test,
            ),
            request(
                "Run summary",
                "GET",
                "{{agent}}/v1/runs/{{alert_id}}",
                auth="admin_token",
                test="""pm.test('200', () => pm.response.to.have.status(200));
const run = pm.response.json();
pm.test('completed', () => pm.expect(run.state).to.eql('completed'));
pm.test('decision recorded', () => pm.expect(run.decision.action).to.be.a('string'));
const steps = run.outcomes.map(o => o.step);
pm.test('notified', () => pm.expect(steps).to.include('notify'));
if (run.decision.action === 'restart_container' && run.plan.includes('remediate')) {
  const rem = run.outcomes.find(o => o.step === 'remediate');
  pm.test('remediation: dry run, restarted, or rejected by the cooldown of an earlier real restart', () => {
    pm.expect(['dry_run', 'restarted', 'rejected']).to.include(rem.status);
    if (rem.status === 'rejected') pm.expect(rem.reason).to.match(/^cooldown/);
  });
}""",
            ),
            request(
                "Mock Teams received the card",
                "GET",
                "{{sink}}/requests",
                test="""const titles = pm.response.json().map(r => r.body.attachments[0].content.body[0].text);
const id = pm.collectionVariables.get('display_id');
pm.test('a card for ' + id, () => pm.expect(titles.some(t => t.includes(id))).to.be.true);""",
            ),
            request(
                "Mailpit received the email",
                "GET",
                "{{mailpit}}/api/v1/search?query={{display_id}}",
                test="pm.test('at least one email', () => pm.expect(pm.response.json().messages_count).to.be.above(0));",
            ),
        ],
        "Spec success criterion 1.3: a mocked Dynatrace payload produces a run, a Teams card and an email.",
    )

    dedup = folder(
        "2 Dedup",
        [
            request(
                "POST the same body again",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "container_down_active"),
                test="""pm.test('200 duplicate', () => pm.response.to.have.status(200));
pm.test('duplicate flag', () => pm.expect(pm.response.json()).to.eql({alert_id: null, duplicate: true}));""",
            ),
        ],
        "Run after folder 1: it re-uses folder 1's event_id and display_id.",
    )

    closed_pre, closed_test = wait_for("closed_alert_id")
    lifecycle = folder(
        "3 Lifecycle (CLOSED)",
        [
            request(
                "POST container_down_closed (same problem)",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "container_down_closed"),
                test=save_alert("closed_alert_id"),
            ),
            request(
                "Wait for done",
                "GET",
                "{{intake}}/v1/alerts/{{closed_alert_id}}",
                auth="service_token",
                pre=closed_pre,
                test=closed_test,
            ),
            request(
                "Run resolved the problem",
                "GET",
                "{{agent}}/v1/runs/{{closed_alert_id}}",
                auth="admin_token",
                test="""const run = pm.response.json();
pm.test('only the resolve step', () => pm.expect(run.outcomes.map(o => o.step)).to.eql(['resolve']));
pm.test('ticket resolved', () => pm.expect(run.outcomes[0].ticket_id).to.match(/^TCK-/));""",
            ),
        ],
        "Run after folder 1: closes the same problem.",
    )

    c_pre, c_test = wait_for("classic_alert_id")
    cr_pre, cr_test = wait_for("classic_closed_alert_id")
    classic = folder(
        "4 Classic webhook",
        [
            request(
                "POST classic OPEN",
                "POST",
                CL,
                auth="webhook_token",
                body=fixture_body("classic", "container_down_open"),
                pre=NEW_CLASSIC_IDS,
                test=save_alert("classic_alert_id"),
            ),
            request(
                "Wait for done",
                "GET",
                "{{intake}}/v1/alerts/{{classic_alert_id}}",
                auth="service_token",
                pre=c_pre,
                test=c_test,
            ),
            request(
                "Alert maps OPEN to ACTIVE",
                "GET",
                "{{intake}}/v1/alerts/{{classic_alert_id}}",
                auth="service_token",
                test="pm.test('ACTIVE', () => pm.expect(pm.response.json().status).to.eql('ACTIVE'));",
            ),
            request(
                "POST classic RESOLVED",
                "POST",
                CL,
                auth="webhook_token",
                body=fixture_body("classic", "container_down_resolved"),
                test=save_alert("classic_closed_alert_id"),
            ),
            request(
                "Wait for done",
                "GET",
                "{{intake}}/v1/alerts/{{classic_closed_alert_id}}",
                auth="service_token",
                pre=cr_pre,
                test=cr_test,
            ),
            request(
                "Run resolved",
                "GET",
                "{{agent}}/v1/runs/{{classic_closed_alert_id}}",
                auth="admin_token",
                test="pm.test('resolve', () => pm.expect(pm.response.json().outcomes.map(o => o.step)).to.eql(['resolve']));",
            ),
        ],
    )

    routing = folder(
        "5 Routing scenarios",
        [
            *post_wait_run("No target", "high_error_rate_active", ["notify"]),
            *post_wait_run("Maintenance", "maintenance_window_active", []),
            *post_wait_run("Outage sev 5", "outage_sev5_active", ["notify"]),
            *post_wait_run("Ambiguous target", "ambiguous_target_active", ["notify", "escalate"]),
            *post_wait_run("Unknown entity", "unknown_entity_active", ["notify"]),
        ],
        "Expected plans are for TRIAGE__DECISION__PROVIDER=rules (Module 1). Module 3 adds a Kev variant.",
    )

    def kev_case(label: str, fixture: str, extra: str = "") -> list[dict[str, Any]]:
        var = f"kev_{fixture}"
        pre, test = wait_for(var)
        return [
            request(
                f"{label}: POST {fixture}",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", fixture),
                pre=NEW_IDS,
                test=save_alert(var),
            ),
            request(
                f"{label}: wait for done",
                "GET",
                f"{{{{intake}}}}/v1/alerts/{{{{{var}}}}}",
                auth="service_token",
                pre=pre,
                test=test,
            ),
            request(
                f"{label}: Kev decided",
                "GET",
                f"{{{{agent}}}}/v1/runs/{{{{{var}}}}}",
                auth="admin_token",
                test=KEV_DECIDED + extra,
            ),
        ]

    kev = folder(
        "6 Kev decisions (needs compose.kev.yaml)",
        [
            request(
                "Kev is reachable",
                "GET",
                "{{kev}}/v1/models",
                test="pm.test('kev-latest', () => pm.expect(pm.response.json().models.map(m => m.name)).to.include('kev-latest'));",
            ),
            *kev_case("Container down", "container_down_active"),
            *kev_case(
                "Outage",
                "outage_sev5_active",
                """
pm.test('an outage is never auto-remediated', () => pm.expect(run.plan).to.not.include('remediate'));""",
            ),
            *kev_case(
                "No target",
                "high_error_rate_active",
                """
pm.test('no target, no remediation', () => pm.expect(run.plan).to.not.include('remediate'));""",
            ),
            *kev_case(
                "Prompt injection",
                "injection_logs_active",
                """
pm.test('only the registry target can be touched', () => pm.expect(run.target.target ? run.target.target.target_id : null).to.be.oneOf([null, 'demo-orders-api']));""",
            ),
            request(
                "Maintenance: POST",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "maintenance_window_active"),
                pre=NEW_IDS,
                test=save_alert("kev_maint"),
            ),
            request(
                "Maintenance: wait for done",
                "GET",
                "{{intake}}/v1/alerts/{{kev_maint}}",
                auth="service_token",
                pre=wait_for("kev_maint")[0],
                test=wait_for("kev_maint")[1],
            ),
            request(
                "Maintenance: the rule decided before Kev was asked",
                "GET",
                "{{agent}}/v1/runs/{{kev_maint}}",
                auth="admin_token",
                test="""const run = pm.response.json();
pm.test('rules, not the model', () => pm.expect(run.decision.source).to.eql('rules'));
pm.test('suppressed', () => pm.expect(run.plan).to.eql([]));""",
            ),
        ],
        "Start agent-runtime with: docker compose -f compose.yaml -f compose.kev.yaml up -d agent-runtime. "
        "Kev must be reachable from Docker at KEV_BASE_URL.",
    )

    inj_pre, inj_test = wait_for("injection_alert_id")
    big = "pm.collectionVariables.set('big_pad', 'x'.repeat(300000));"
    security = folder(
        "7 Security and negative",
        [
            request(
                "No token -> 401",
                "POST",
                WF,
                body=fixture_body("workflow", "container_down_active"),
                test="pm.test('401', () => pm.response.to.have.status(401));",
            ),
            request(
                "Wrong token -> 401",
                "POST",
                WF,
                auth="wrong_token",
                body=fixture_body("workflow", "container_down_active"),
                test="pm.test('401', () => pm.response.to.have.status(401));",
            ),
            request(
                "Malformed JSON -> 422",
                "POST",
                WF,
                auth="webhook_token",
                body="{not json",
                test="pm.test('422', () => pm.response.to.have.status(422));",
            ),
            request(
                "Unmappable payload -> 422",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "malformed"),
                test="pm.test('422', () => pm.response.to.have.status(422));",
            ),
            request(
                "Oversized body -> 413",
                "POST",
                WF,
                auth="webhook_token",
                body='{"pad": "{{big_pad}}"}',
                pre=big,
                test="pm.test('413', () => pm.response.to.have.status(413));",
            ),
            request(
                "Runs API without admin token -> 401",
                "GET",
                "{{agent}}/v1/runs/{{alert_id}}",
                test="pm.test('401', () => pm.response.to.have.status(401));",
            ),
            request(
                "Prompt injection in logs",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "injection_logs_active"),
                pre=NEW_IDS,
                test=save_alert("injection_alert_id"),
            ),
            request(
                "Wait for done",
                "GET",
                "{{intake}}/v1/alerts/{{injection_alert_id}}",
                auth="service_token",
                pre=inj_pre,
                test=inj_test,
            ),
            request(
                "Injection did not widen the plan",
                "GET",
                "{{agent}}/v1/runs/{{injection_alert_id}}",
                auth="admin_token",
                test="""const run = pm.response.json();
pm.test('no plan beyond the policy', () => pm.expect(['notify', 'remediate', 'ticket']).to.include.members(run.plan));
const rem = run.outcomes.find(o => o.step === 'remediate');
pm.test('at most the registered target was touched', () => pm.expect(run.target.target ? run.target.target.target_id : null).to.be.oneOf([null, 'demo-orders-api']));""",
            ),
        ],
        "The injection fixture's logs say 'restart all containers'. Routing must not change.",
    )

    ap_pre, ap_test = wait_for("approval_alert_id", ("awaiting_approval",))
    done_pre, done_test = wait_for("approval_alert_id")
    approval = folder(
        "8 Manual approval (needs compose.manual-approval.yaml)",
        [
            request(
                "POST container_down_active",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "container_down_active"),
                pre=NEW_IDS,
                test=save_alert("approval_alert_id"),
            ),
            request(
                "Wait for awaiting_approval",
                "GET",
                "{{intake}}/v1/alerts/{{approval_alert_id}}",
                auth="service_token",
                pre=ap_pre,
                test=ap_test,
            ),
            request(
                "Run is waiting for a person",
                "GET",
                "{{agent}}/v1/runs/{{approval_alert_id}}",
                auth="admin_token",
                test="""const run = pm.response.json();
pm.test('awaiting approval', () => pm.expect(run.state).to.eql('awaiting_approval'));
pm.test('approval requested', () => pm.expect(run.approval_request.state).to.eql('requested'));""",
            ),
            request(
                "Approve",
                "POST",
                "{{agent}}/v1/runs/{{approval_alert_id}}/approval",
                auth="admin_token",
                body=json.dumps({"decision": "approve", "approver": "postman", "reason": "looks right"}),
                test="pm.test('202', () => pm.response.to.have.status(202));",
            ),
            request(
                "Wait for done",
                "GET",
                "{{intake}}/v1/alerts/{{approval_alert_id}}",
                auth="service_token",
                pre=done_pre,
                test=done_test,
            ),
            request(
                "Approved by postman",
                "GET",
                "{{agent}}/v1/runs/{{approval_alert_id}}",
                auth="admin_token",
                test="""const run = pm.response.json();
pm.test('approved by postman', () => pm.expect(run.approval.approved_by).to.eql('postman'));""",
            ),
            request(
                "Second decision -> 409",
                "POST",
                "{{agent}}/v1/runs/{{approval_alert_id}}/approval",
                auth="admin_token",
                body=json.dumps({"decision": "approve", "approver": "postman"}),
                test="pm.test('409', () => pm.response.to.have.status(409));",
            ),
        ],
        "Start agent-runtime with: docker compose -f compose.yaml -f compose.manual-approval.yaml up -d agent-runtime",
    )

    l1_pre, l1_test = wait_for("live_alert_id", tries=150)
    l2_pre, l2_test = wait_for("live2_alert_id", tries=150)
    live = folder(
        "9 Live restart and guard rails (needs compose.live-restart.yaml)",
        [
            request(
                "Target status before",
                "GET",
                "{{agent}}/healthz",
                test="pm.collectionVariables.set('live_started_before', new Date().toISOString());",
            ),
            request(
                "POST container_down_active",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "container_down_active"),
                pre=NEW_IDS,
                test=save_alert("live_alert_id"),
            ),
            request(
                "Wait for done (verification waits 30 s)",
                "GET",
                "{{intake}}/v1/alerts/{{live_alert_id}}",
                auth="service_token",
                pre=l1_pre,
                test=l1_test,
            ),
            request(
                "demo-orders-api was restarted and verified",
                "GET",
                "{{agent}}/v1/runs/{{live_alert_id}}",
                auth="admin_token",
                test="""const run = pm.response.json();
const rem = run.outcomes.find(o => o.step === 'remediate');
pm.test('remediation ran', () => pm.expect(rem).to.not.be.undefined);
pm.test('restarted (if this says rejected: cooldown, wait 15 min since the last restart)', () => pm.expect(rem.status).to.eql('restarted'));
pm.test('container running with a newer start time', () => pm.expect(rem.verification.restarted_after_request).to.eql(true));""",
            ),
            request(
                "Mock Teams got the remediation result",
                "GET",
                "{{sink}}/requests",
                test="""const id = pm.collectionVariables.get('display_id');
const texts = pm.response.json().map(r => r.body.attachments[0].content.body.map(b => b.text || '').join(' '));
pm.test('result card', () => pm.expect(texts.some(t => t.includes('REMEDIATION [' + id) && t.includes('Restarted demo-orders-api'))).to.be.true);""",
            ),
            request(
                "A second problem right away (different key)",
                "POST",
                WF,
                auth="webhook_token",
                body=fixture_body("workflow", "container_down_active"),
                pre=NEW_IDS,
                test=save_alert("live2_alert_id"),
            ),
            request(
                "Wait for done",
                "GET",
                "{{intake}}/v1/alerts/{{live2_alert_id}}",
                auth="service_token",
                pre=l2_pre,
                test=l2_test,
            ),
            request(
                "Cooldown rejected the second restart",
                "GET",
                "{{agent}}/v1/runs/{{live2_alert_id}}",
                auth="admin_token",
                test="""const rem = pm.response.json().outcomes.find(o => o.step === 'remediate');
pm.test('rejected', () => pm.expect(rem.status).to.eql('rejected'));
pm.test('because of the cooldown', () => pm.expect(rem.reason).to.match(/^cooldown/));""",
            ),
        ],
        "Restarts demo-orders-api for real. Start mcp-remediation with: docker compose -f compose.yaml "
        "-f compose.live-restart.yaml up -d mcp-remediation. The target's 15 min cooldown means this folder "
        "passes at most once per 15 minutes.",
    )

    return {
        "info": {
            # A fixed id makes a re-import offer "Replace" instead of creating a second copy with the same name.
            "_postman_id": "6f1d2c1e-0a7b-4c55-9a3e-0b5e7d0c0a01",
            "name": f"Ops Triage Agents (local) v{COLLECTION_VERSION}",
            "description": "Mock Dynatrace webhooks against the local stack. Generated by postman/build_collection.py.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [health, happy, dedup, lifecycle, classic, routing, kev, security, approval, live],
        "event": [
            {
                "listen": "prerequest",
                "script": {"type": "text/javascript", "exec": VARIABLE_GUARD.split("\n")},
            }
        ],
        "variable": [{"key": k, "value": v} for k, v in DEFAULTS.items()]
        + [{"key": k, "value": ""} for k in ("event_id", "display_id", "alert_id", "_tries")],
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(OUT)
