from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError

from opstriage_contracts.problems import ProblemEvent
from opstriage_intake.adapters import dynatrace_classic, dynatrace_workflow
from opstriage_intake.application.get_alert import GetAlert
from opstriage_intake.application.receive_problem_event import ReceiveProblemEvent
from opstriage_intake.ports.fixtures import FixtureLoader, FixtureNotFound

Mapper = Callable[[dict[str, Any]], ProblemEvent]
MAPPERS: dict[str, Mapper] = {
    "workflow": dynatrace_workflow.to_problem_event,
    "classic": dynatrace_classic.to_problem_event,
}


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "body is not valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "body must be a JSON object")
    return body


def _map(kind: str, body: dict[str, Any]) -> ProblemEvent:
    try:
        return MAPPERS[kind](body)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def build_router(
    receive: ReceiveProblemEvent,
    get_alert: GetAlert,
    webhook_guard: Callable[..., None],
    service_guard: Callable[..., None],
    fixtures: FixtureLoader | None,
) -> APIRouter:
    router = APIRouter(prefix="/v1")

    async def _accept(event: ProblemEvent, raw: dict[str, Any] | None, response: Response) -> dict[str, Any]:
        result = await receive(event, raw)
        response.status_code = status.HTTP_200_OK if result.duplicate else status.HTTP_202_ACCEPTED
        return {"alert_id": str(result.alert_id) if result.alert_id else None, "duplicate": result.duplicate}

    @router.post("/webhooks/dynatrace/workflow", dependencies=[Depends(webhook_guard)])
    async def workflow(request: Request, response: Response) -> dict[str, Any]:
        body = await _json_object(request)
        return await _accept(_map("workflow", body), body, response)

    @router.post("/webhooks/dynatrace/classic", dependencies=[Depends(webhook_guard)])
    async def classic(request: Request, response: Response) -> dict[str, Any]:
        body = await _json_object(request)
        return await _accept(_map("classic", body), body, response)

    @router.post("/alerts", dependencies=[Depends(service_guard)])
    async def canonical(event: ProblemEvent, response: Response) -> dict[str, Any]:
        return await _accept(event, None, response)

    @router.get("/alerts/{alert_id}", dependencies=[Depends(service_guard)])
    async def alert(alert_id: UUID) -> dict[str, Any]:
        view = await get_alert(alert_id)
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown alert")
        return {
            "alert_id": str(view.alert_id),
            "problem_id": view.problem_id,
            "display_id": view.display_id,
            "status": view.status,
            "source": view.source,
            "state": view.state.value,
            "attempts": view.attempts,
            "received_at": view.received_at.isoformat(),
            "last_error": view.last_error,
        }

    if fixtures is not None:
        loader = fixtures

        @router.post("/dev/simulate/{kind}/{fixture}", dependencies=[Depends(webhook_guard)])
        async def simulate(kind: str, fixture: str, response: Response) -> dict[str, Any]:
            if kind not in MAPPERS:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown fixture kind")
            try:
                body = await loader.load(kind, fixture)
            except FixtureNotFound as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown fixture") from exc
            return await _accept(_map(kind, body), body, response)

    return router
