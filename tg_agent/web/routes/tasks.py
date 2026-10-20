"""Interop task REST API (#961, part of #829).

Lets an external tg_messenger worker create, poll, atomically claim, and report
back on interop tasks (dm_reply / chat_answer / fetch_dialogs / fetch_history).

Auth: mounted behind the existing web auth middleware (HTTP Basic with WEB_PASS
for non-browser clients), so no separate gate here.

Gating: only EXTERNAL_INTEROP_TASK_TYPES may be created or claimed through this
API. The factory's own task types stay internal — an external worker can neither
inject them nor steal them.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from tg_agent.models import EXTERNAL_INTEROP_TASK_TYPES, CollectionTaskStatus, CollectionTaskType
from tg_agent.utils.json import safe_json_dumps
from tg_agent.web import deps

router = APIRouter()

_ALLOWED_VALUES = {t.value for t in EXTERNAL_INTEROP_TASK_TYPES}
# Cap JSON bodies so an authenticated client can't bloat the SQLite TEXT column
# (or stress JSON (de)serialization) with a multi-MB payload (#961 review).
_MAX_PAYLOAD_BYTES = 64 * 1024


def _check_payload_size(value: dict) -> dict:
    if len(safe_json_dumps(value).encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds {_MAX_PAYLOAD_BYTES} bytes")
    return value


class CreateTaskRequest(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)

    _size = field_validator("payload")(_check_payload_size)


class ClaimRequest(BaseModel):
    types: list[str] = Field(default_factory=list)
    # Lease/TTL recovery (#1468): the claiming worker identifies itself and
    # bounds its lease. A stranded RUNNING task (its holder died after a lost
    # claim response) becomes claimable again only once `lease_seconds` have
    # passed since `claimed_at` — never while the lease is live.
    claimed_by: str | None = Field(default=None, min_length=1, max_length=255)
    lease_seconds: int | None = Field(default=None, ge=1, le=7 * 24 * 3600)

    @model_validator(mode="after")
    def _lease_needs_owner(self) -> ClaimRequest:
        if self.lease_seconds is not None and self.claimed_by is None:
            raise ValueError("lease_seconds requires claimed_by")
        return self


class CompleteRequest(BaseModel):
    result_payload: dict = Field(default_factory=dict)

    _size = field_validator("result_payload")(_check_payload_size)


class FailRequest(BaseModel):
    error: str


def _require_external(type_value: str) -> CollectionTaskType:
    if type_value not in _ALLOWED_VALUES:
        raise HTTPException(
            status_code=403,
            detail=f"Task type '{type_value}' is not claimable via the interop API",
        )
    return CollectionTaskType(type_value)


def _task_json(task) -> dict:
    return task.model_dump(mode="json")


@router.post("")
async def create_task(request: Request, body: CreateTaskRequest):
    task_type = _require_external(body.type)
    tasks = deps.get_db(request).repos.tasks
    task_id = await tasks.create_generic_task(
        task_type, payload=body.payload, idempotency_key=body.idempotency_key
    )
    return JSONResponse({"id": task_id}, status_code=201)


@router.post("/claim")
async def claim_task(request: Request, body: ClaimRequest):
    requested = body.types or list(_ALLOWED_VALUES)
    for type_value in requested:
        _require_external(type_value)
    tasks = deps.get_db(request).repos.tasks
    # Types are already gated to the external allow-list above; the dispatcher's
    # HANDLED_TYPES never includes them, so this claim only ever races other
    # external workers, not the factory's own dispatcher.
    task = await tasks.claim_next_due_generic_task(
        datetime.now(timezone.utc),
        requested,
        claimed_by=body.claimed_by,
        lease_seconds=body.lease_seconds,
    )
    if task is None:
        return Response(status_code=204)
    return JSONResponse(_task_json(task))


# NOTE (orphan recovery, #1468): a RUNNING interop task is reclaimable through
# POST /claim once its lease expires — pass `claimed_by` + `lease_seconds` and
# a task whose holder died mid-task (lost claim response) is handed to the
# retrying worker after TTL expiry, with ownership transferred. Live leases are
# never stolen, and ownerless RUNNING rows (internal claims) are untouched, so
# peers' in-flight tasks stay safe with multiple workers (Codex review on #961).
async def _load_external_task(tasks, task_id: int):
    """Fetch a task and gate it to the external interop allow-list.

    Without this, an authenticated external worker could read or complete the
    factory's *internal* tasks (channel_collect, pipeline_run, …) by id — info
    disclosure + lifecycle poisoning (review on #961).
    """
    task = await tasks.get_collection_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    _require_external(task.task_type.value)
    return task


@router.get("/{task_id}")
async def get_task(request: Request, task_id: int):
    tasks = deps.get_db(request).repos.tasks
    task = await _load_external_task(tasks, task_id)
    return JSONResponse(_task_json(task))


@router.post("/{task_id}/complete")
async def complete_task(request: Request, task_id: int, body: CompleteRequest):
    tasks = deps.get_db(request).repos.tasks
    await _load_external_task(tasks, task_id)
    # Only RUNNING tasks can change. After a lost response (or concurrent report),
    # reload the winning outcome rather than overwriting a terminal task.
    updated = await tasks.update_collection_task(
        task_id,
        CollectionTaskStatus.COMPLETED,
        result_payload=body.result_payload,
        required_status=CollectionTaskStatus.RUNNING,
    )
    if not updated:
        task = await _load_external_task(tasks, task_id)
        if task.status != CollectionTaskStatus.COMPLETED or safe_json_dumps(
            task.result_payload, sort_keys=True
        ) != safe_json_dumps(body.result_payload, sort_keys=True):
            raise HTTPException(status_code=409, detail="Task is not RUNNING or completion result conflicts")
    return JSONResponse({"ok": True})


@router.post("/{task_id}/fail")
async def fail_task(request: Request, task_id: int, body: FailRequest):
    tasks = deps.get_db(request).repos.tasks
    await _load_external_task(tasks, task_id)
    updated = await tasks.update_collection_task(
        task_id,
        CollectionTaskStatus.FAILED,
        error=body.error,
        required_status=CollectionTaskStatus.RUNNING,
    )
    if not updated:
        task = await _load_external_task(tasks, task_id)
        if task.status != CollectionTaskStatus.FAILED or task.error != body.error:
            raise HTTPException(status_code=409, detail="Task is not RUNNING or failure report conflicts")
    return JSONResponse({"ok": True})
