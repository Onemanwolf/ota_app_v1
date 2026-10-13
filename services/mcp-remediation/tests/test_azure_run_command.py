"""azure_run_command executor against a scripted ARM endpoint: request shape, async polling, retries, timeouts and
the safety properties (registry-only container name, no shell interpolation, token only to ARM, never logged)."""

import json
import logging
from types import SimpleNamespace

import pytest
import structlog

from opstriage_remediation.adapters.azure_run_command import interpret_output, parse_retry_after
from opstriage_remediation.ports.executor import ExecutorUnavailable
from opstriage_shared.credentials import SCOPE_ARM


async def test_request_url_body_and_bearer(arm_kit: SimpleNamespace) -> None:
    arm = arm_kit.FakeArm()
    executor, creds = arm_kit.make_executor(arm)
    outcome = await executor.restart(arm_kit.azure_target(), timeout_s=20)
    await executor.close()

    assert outcome.succeeded
    post = arm.requests[0]
    assert post.method == "POST"
    assert str(post.url) == f"{arm_kit.ARM}{arm_kit.VM_ID}/runCommand?api-version=2024-07-01"
    assert post.headers["Authorization"] == f"Bearer {arm_kit.TOKEN}"
    assert json.loads(post.content) == {
        "commandId": "RunShellScript",
        "script": ['/usr/local/sbin/restart-allowed.sh "$1" "$2"'],  # constant: no request data in the script
        "parameters": [{"name": "arg1", "value": "demo-orders-api"}, {"name": "arg2", "value": "20"}],
    }
    assert set(creds.scopes) == {SCOPE_ARM}
    assert all(r.headers["Authorization"] == f"Bearer {arm_kit.TOKEN}" for r in arm.requests)
    assert all(str(r.url).startswith(f"{arm_kit.ARM}/") for r in arm.requests)


async def test_async_polling_honours_retry_after(arm_kit: SimpleNamespace) -> None:
    arm = arm_kit.FakeArm(in_progress_polls=3, retry_after="7")
    time = arm_kit.FakeTime()
    executor, _ = arm_kit.make_executor(arm, time)
    outcome = await executor.restart(arm_kit.azure_target(), timeout_s=20)
    assert outcome.succeeded and "exit_code=0" in (outcome.output_tail or "")
    assert time.sleeps == [7.0, 7.0, 7.0, 7.0]  # after the 202 and after each InProgress
    assert [r.method for r in arm.requests] == ["POST", "GET", "GET", "GET", "GET"]
    assert all(str(r.url) == arm_kit.OP_URL for r in arm.requests[1:])


async def test_default_poll_interval_without_retry_after(arm_kit: SimpleNamespace) -> None:
    time = arm_kit.FakeTime()
    executor, _ = arm_kit.make_executor(arm_kit.FakeArm(retry_after=None), time, poll_interval_seconds=4)
    assert (await executor.restart(arm_kit.azure_target(), timeout_s=20)).succeeded
    assert time.sleeps == [4.0, 4.0]


async def test_result_from_location_when_operation_has_no_output(arm_kit: SimpleNamespace) -> None:
    arm = arm_kit.FakeArm(output_in_operation=False)
    executor, _ = arm_kit.make_executor(arm)
    assert (await executor.restart(arm_kit.azure_target(), timeout_s=20)).succeeded
    assert str(arm.requests[-1].url) == arm_kit.RESULT_URL


async def test_script_failure_is_a_failed_outcome(arm_kit: SimpleNamespace) -> None:
    executor, _ = arm_kit.make_executor(arm_kit.FakeArm(allowed=set()))
    outcome = await executor.restart(arm_kit.azure_target(), timeout_s=20)
    assert not outcome.succeeded
    assert "not allow-listed: demo-orders-api" in (outcome.output_tail or "")
    assert "exit_code=65" in (outcome.output_tail or "")


async def test_arm_operation_failed_reports_code_only(arm_kit: SimpleNamespace) -> None:
    executor, _ = arm_kit.make_executor(arm_kit.FakeArm(arm_status="Failed"))
    outcome = await executor.restart(arm_kit.azure_target(), timeout_s=20)
    assert not outcome.succeeded
    assert outcome.output_tail == "arm_status=failed error_code=VMAgentStatusCommunicationError"


def test_output_parsing_needs_explicit_exit_code_zero_on_stdout() -> None:
    assert interpret_output(["Enable succeeded: \n[stdout]\nok\nexit_code=0\n[stderr]\n"]).succeeded
    assert not interpret_output(
        ["Enable succeeded: \n[stdout]\nexit_code=0\nexit_code=70\n[stderr]\n"]
    ).succeeded
    assert not interpret_output(["Enable succeeded: \n[stdout]\n\n[stderr]\nexit_code=0\n"]).succeeded
    assert not interpret_output(["Enable succeeded: \n[stdout]\nno code\n"]).succeeded
    long = interpret_output(["[stdout]\n" + "x" * 10_000 + "\nexit_code=0\n"])
    assert long.succeeded and len((long.output_tail or "").encode()) <= 4096


async def test_timeout_stops_polling_and_says_restart_may_complete(arm_kit: SimpleNamespace) -> None:
    time = arm_kit.FakeTime()
    executor, _ = arm_kit.make_executor(
        arm_kit.FakeArm(in_progress_polls=10_000, retry_after="10"), time, run_command_overhead_seconds=30
    )
    with pytest.raises(ExecutorUnavailable, match="may still complete"):
        await executor.restart(arm_kit.azure_target(timeout=20), timeout_s=20)
    assert time.now <= 20 + 30  # never sleeps past the budget (restart timeout + overhead)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_errors_are_retried_with_retry_after(arm_kit: SimpleNamespace, status: int) -> None:
    arm = arm_kit.FakeArm(post_errors=[status, status], poll_errors=[status])
    time = arm_kit.FakeTime()
    executor, _ = arm_kit.make_executor(arm, time)
    assert (await executor.restart(arm_kit.azure_target(), timeout_s=20)).succeeded
    assert [r.method for r in arm.requests][:3] == ["POST", "POST", "POST"]
    if status in (429, 503):  # these answers carry Retry-After: 3
        assert time.sleeps[:2] == [3.0, 3.0]


async def test_transient_errors_give_up_after_max_attempts(arm_kit: SimpleNamespace) -> None:
    arm = arm_kit.FakeArm(post_errors=[503] * 10)
    executor, _ = arm_kit.make_executor(arm, max_attempts=3)
    with pytest.raises(ExecutorUnavailable, match="HTTP 503"):
        await executor.restart(arm_kit.azure_target(), timeout_s=20)
    assert len(arm.requests) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
async def test_client_errors_are_permanent(arm_kit: SimpleNamespace, status: int) -> None:
    arm = arm_kit.FakeArm(post_errors=[status])
    executor, _ = arm_kit.make_executor(arm)
    with pytest.raises(ExecutorUnavailable, match=f"HTTP {status} Code{status}"):
        await executor.restart(arm_kit.azure_target(), timeout_s=20)
    assert len(arm.requests) == 1


@pytest.mark.parametrize(
    "container",
    [
        "orders-api; reboot",
        "orders-api$(id)",
        "orders-api\nreboot",
        "orders-api`id`",
        "Orders-API",
        "-rf",
        "a" * 64,
        "",
        "orders api",
        "../etc",
    ],
)
async def test_injection_in_container_name_rejected_before_any_http(
    arm_kit: SimpleNamespace, container: str
) -> None:
    arm = arm_kit.FakeArm()
    executor, creds = arm_kit.make_executor(arm)
    with pytest.raises(ExecutorUnavailable, match="invalid container name"):
        await executor.restart(arm_kit.azure_target(container=container), timeout_s=20)
    assert arm.requests == [] and creds.scopes == []  # no token fetched, nothing sent


@pytest.mark.parametrize(
    "resource_id",
    [
        None,
        "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Compute/virtualMachines/<vm>",  # placeholder
        "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg/providers/"
        "Microsoft.Compute/virtualMachines/vm/../../other",
        "/subscriptions/99999999-1111-2222-3333-444444444444/resourceGroups/rg/providers/"
        "Microsoft.Compute/virtualMachines/vm",  # other subscription than REMEDIATION__AZURE__SUBSCRIPTION_ID
        "https://evil.example/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg/providers/"
        "Microsoft.Compute/virtualMachines/vm",
    ],
)
async def test_bad_resource_id_rejected_before_any_http(
    arm_kit: SimpleNamespace, resource_id: str | None
) -> None:
    arm = arm_kit.FakeArm()
    executor, creds = arm_kit.make_executor(arm)
    with pytest.raises(ExecutorUnavailable):
        await executor.restart(arm_kit.azure_target(resource_id=resource_id), timeout_s=20)
    assert arm.requests == [] and creds.scopes == []


async def test_status_url_outside_arm_is_refused(arm_kit: SimpleNamespace) -> None:
    arm = arm_kit.FakeArm()
    original = arm.handler

    def handler(request):  # type: ignore[no-untyped-def]
        resp = original(request)
        if request.method == "POST":
            resp.headers["Azure-AsyncOperation"] = "https://evil.example/op"
        return resp

    arm.handler = handler  # type: ignore[method-assign]
    executor, _ = arm_kit.make_executor(arm)
    with pytest.raises(ExecutorUnavailable, match="outside the ARM endpoint"):
        await executor.restart(arm_kit.azure_target(), timeout_s=20)
    assert len(arm.requests) == 1  # the token never went to the other host


async def test_status_is_not_observable(arm_kit: SimpleNamespace) -> None:
    arm = arm_kit.FakeArm()
    executor, _ = arm_kit.make_executor(arm)
    with pytest.raises(ExecutorUnavailable):
        await executor.status(arm_kit.azure_target())
    assert arm.requests == []


async def test_tokens_and_error_messages_never_logged(
    arm_kit: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    executors = []
    with structlog.testing.capture_logs() as logs:
        for arm in (
            arm_kit.FakeArm(),
            arm_kit.FakeArm(arm_status="Failed"),
            arm_kit.FakeArm(post_errors=[403]),
            arm_kit.FakeArm(post_errors=[503] * 10),
        ):
            executor, _ = arm_kit.make_executor(arm)
            executors.append(executor)
            try:
                outcome = await executor.restart(arm_kit.azure_target(), timeout_s=20)
                assert arm_kit.TOKEN not in (outcome.output_tail or "")
            except ExecutorUnavailable as exc:
                assert arm_kit.TOKEN not in str(exc)
    text = repr(logs) + caplog.text
    assert logs  # the executor does log (events, ids, codes)
    assert arm_kit.TOKEN not in text and "Bearer" not in text and "secret detail" not in text
    for executor in executors:
        await executor.close()


def test_retry_after_parsing() -> None:
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # in the past
