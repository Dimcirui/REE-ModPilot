"""
FastAPI application entry point.

Debug endpoints (Stage 1):
  GET  /health              — liveness + Blender connectivity check; 503 if Blender unreachable
  GET  /scene_info          — proxy get_scene_info from Blender
  GET  /viewport_screenshot — PNG bytes of the active 3D viewport (Stage 5 side-panel)
  POST /exec                — execute arbitrary Python in Blender (DEBUG mode only)

Agent endpoints:
  POST /agent/chat            — Stage 3 legacy; blocking JSON. Used by cli.py.
  POST /agent/messages        — Stage 5; same JSON shape, but installs an event
                                sink so SSE subscribers see live progress.
  GET  /agent/stream/{sid}    — Stage 5; SSE stream of typed agent events.

Frontend (Stage 5):
  GET  /            — renders the htmx chat shell with a fresh session_id.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import uuid

logger = logging.getLogger(__name__)
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from app.agent.loop import AgentLoop
from app.blender.client import BlenderBusyError, BlenderClient, BlenderError
from app.blender.preset_catalog import (
    SHIPPED_X_PRESETS,
    PresetMeta,
    discover_preset_dir,
    enumerate_x_presets,
)
from app.config import settings
from app.config_store import PERSISTED_FIELDS, apply_to_settings
from app.config_store import load as load_persisted_config
from app.config_store import save as save_persisted_config
from app.llm.client import LLMClient
from app.phases.base import X_PRESETS, update_x_presets
from app.phases.material import PRINCIPLED_SLOTS
from app.phases.physics_bones import list_inferred_types

# ── paths ─────────────────────────────────────────────────────────────────

_APP_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _APP_DIR.parent  # ModPilot/
_STATIC_DIR = _PROJECT_DIR / "static"
_TEMPLATES_DIR = _APP_DIR / "templates"


# ── lifespan: manage shared BlenderClient + agent session/stream registries ──


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Open one BlenderClient and one LLMClient for the server lifetime.
    Both are stored on app.state for route handler access.

    Also initializes per-session registries:
      - agent_sessions: AgentLoop per session_id (lazy)
      - agent_streams:  asyncio.Queue of structured events per session_id (lazy)

    If Blender is not running at startup, the server still starts — endpoints
    will return 503 until Blender becomes available.
    """
    # Issue #9: layer persisted user config on top of the .env defaults.
    persisted = load_persisted_config()
    if persisted:
        apply_to_settings(settings, persisted)

    client = BlenderClient(host=settings.blender_host, port=settings.blender_port)
    with contextlib.suppress(OSError):
        client.connect()
    app.state.blender = client
    # Issue #9: tolerate missing api_key at startup so first-run users can
    # reach /config without crashing the server. _require_llm() raises 503
    # when callers actually need the LLM.
    try:
        app.state.llm = LLMClient.from_settings()
    except Exception:
        app.state.llm = None
    app.state.agent_sessions: dict[str, AgentLoop] = {}
    app.state.agent_streams: dict[str, asyncio.Queue] = {}
    app.state.session_configs: dict[str, SessionConfig] = {}

    # Issue #4 foundation: enumerate the toolkit's X-presets so the
    # session-config dropdown and the inference phase can both reference
    # whatever is actually installed. Falls back to the shipped name list
    # when Blender isn't reachable so server boot is non-fatal.
    app.state.x_preset_catalog: dict[str, PresetMeta] = {}
    if client.connected:
        # Discovery is best-effort: any failure (missing addon, partially-mocked
        # client in tests, JSON decode errors) falls through to the shipped
        # list so the server still boots.
        try:
            preset_dir = discover_preset_dir(client)
            app.state.x_preset_catalog = enumerate_x_presets(preset_dir)
        except Exception:
            app.state.x_preset_catalog = {}
    if app.state.x_preset_catalog:
        update_x_presets(app.state.x_preset_catalog.keys())
    else:
        update_x_presets(SHIPPED_X_PRESETS)

    yield

    client.close()


# ── app factory ────────────────────────────────────────────────────────────

app = FastAPI(
    title="ModPilot",
    description="AI-guided Blender automation for RE Engine character mods",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


# ── helpers ────────────────────────────────────────────────────────────────

_BLENDER_HINT = (
    "Blender is not reachable. "
    "Start Blender, enable the blender-mcp addon, and click 'Connect to Claude'."
)

_QUEUE_MAXSIZE = 256


def _render_error_choice_html(session_id: str, category: str = "") -> str:
    """Render the error-choice button fragment for a session.

    Default: retry / skip / 查看详情 three-button group (issue #2).
    When category == "unsupported_rig" (issue #6 0-match path), a fourth
    [强制自定义] button is appended that sends `[FORCE_CUSTOM]` so the LLM
    re-runs setup_infer_model_type with force_custom=true and proceeds
    straight into the custom-preset build.

    Returned HTML is shipped raw as the SSE data field so htmx's `sse-swap`
    can drop it into #error-choice-slot directly. Each button posts the
    matching keyword to /agent/messages (not /agent/chat) so the
    retry/skip/ask turn keeps streaming via the session's sink.
    """
    sid = html.escape(session_id, quote=True)
    # Removal of the .error-choice-group is handled by app.js on
    # htmx:beforeRequest, NOT by inline onclick. Inline onclick removes the
    # button before htmx fires configRequest, which makes htmx bail on the
    # detached element and skips the optimistic-bubble path.
    #
    # hx-ext="json-enc" is set per-button rather than inherited from <body>
    # because htmx 1.x's hx-ext inheritance walk doesn't reliably apply
    # `encodeParameters` to dynamically-inserted (htmx.process'd) descendants
    # — symptom: Content-Type header is correct but body stays form-urlencoded.
    # The endpoint's Pydantic body expects JSON; mismatched encoding -> 422.
    buttons = [
        f'<button class="error-choice-btn retry" hx-ext="json-enc" hx-post="/agent/messages" '
        f'hx-vals=\'{{"session_id":"{sid}","message":"重试"}}\' hx-swap="none">重试</button>',
        f'<button class="error-choice-btn skip" hx-ext="json-enc" hx-post="/agent/messages" '
        f'hx-vals=\'{{"session_id":"{sid}","message":"跳过"}}\' hx-swap="none">跳过</button>',
        f'<button class="error-choice-btn ask" hx-ext="json-enc" hx-post="/agent/messages" '
        f'hx-vals=\'{{"session_id":"{sid}","message":"查看详情"}}\' hx-swap="none">查看详情</button>',
    ]
    if category == "unsupported_rig":
        # Issue #6: let the user force-custom past a 0-match unsupported_rig
        # error. The LLM (per agent_workflow.md) recognizes the [FORCE_CUSTOM]
        # prefix and re-runs setup_infer_model_type with force_custom=true.
        buttons.append(
            f'<button class="error-choice-btn force-custom" hx-ext="json-enc" '
            f'hx-post="/agent/messages" '
            f'hx-vals=\'{{"session_id":"{sid}","message":"[FORCE_CUSTOM] 强制自定义预设"}}\' '
            f'hx-swap="none">强制自定义</button>'
        )
    return '<div class="error-choice-group" role="group">' + "".join(buttons) + '</div>'


def _render_classification_widget_html(session_id: str, chains: list[dict]) -> str:
    """Render the physics chain classification widget (issue #7).

    Shipped over SSE as the raw `data:` field for `widget_classification`
    events; htmx `sse-swap` drops it into `#widget-slot`. The embedded form
    posts back to /agent/widget/classification which formats the user's
    selections as a `[CONFIRMED_CLASSIFICATIONS]`-prefixed message and
    feeds it through loop.step().
    """
    return templates.get_template("widgets/classification.html").render(
        session_id=session_id,
        chains=chains,
        inferred_types=list_inferred_types(),
    )


def _render_material_widget_html(
    session_id: str,
    materials: list[str],
    existing_connections: dict,
    texture_files: list[str],
) -> str:
    """Render the material slot→texture confirmation widget (issue #7)."""
    return templates.get_template("widgets/material.html").render(
        session_id=session_id,
        materials=materials,
        existing_connections=existing_connections,
        texture_files=texture_files,
        slot_names=PRINCIPLED_SLOTS,
        tex_basename=lambda p: Path(p).name,
    )


def _get_client() -> BlenderClient:
    """Return the shared BlenderClient, attempting reconnect if disconnected."""
    client: BlenderClient = app.state.blender
    if not client.connected:
        try:
            client.connect()
        except OSError as exc:
            raise HTTPException(status_code=503, detail=_BLENDER_HINT) from exc
    return client


def _require_llm() -> LLMClient:
    """Return app.state.llm, raising 503 if the user hasn't configured one yet."""
    llm = getattr(app.state, "llm", None)
    if llm is None:
        raise HTTPException(
            status_code=503,
            detail="LLM is not configured. Visit /config to set provider + API key.",
        )
    return llm


def _is_llm_configured() -> bool:
    """Cheap check used by the first-run redirect on `GET /`."""
    return bool(settings.llm_api_key) and getattr(app.state, "llm", None) is not None


async def _run_step_with_done_emit(
    loop: "AgentLoop",
    session_id: str,
    message: str,
) -> str:
    """Call loop.step() and ALWAYS emit a final `done` SSE event afterwards.

    Used by every route that drives the loop (/agent/messages and both widget
    submission routes).  The done event is what unsticks the frontend's
    "thinking" status — missing it makes the chat input look frozen.

    Three failure modes are all handled by the finally block:
      1. step() raises  → emit agent_error, then done, then re-raise as 500.
      2. step() returns normally → emit done.
      3. The session's queue was garbage-collected mid-flight (suspected) →
         re-create it in streams dict so done has somewhere to land.

    Returns the reply string from step (or "" on failure).
    """
    streams: dict[str, asyncio.Queue] = app.state.agent_streams
    reply: str = ""
    failed: Exception | None = None
    try:
        reply = await loop.step(message)
    except Exception as exc:
        failed = exc
        logger.exception("Unhandled exception in loop.step() for session %s", session_id)
    finally:
        queue = streams.get(session_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
            streams[session_id] = queue
        if failed is not None:
            err_evt = {
                "type": "agent_error",
                "ts": 0.0,
                "phase": loop.current_phase,
                "state": loop.state.value,
                "message": str(failed),
                "where": "step",
                "recoverable": False,
            }
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(err_evt)
        done_evt = {
            "type": "done",
            "ts": 0.0,
            "phase": loop.current_phase,
            "state": loop.state.value,
            "reply": reply,
            "session_id": session_id,
        }
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(done_evt)

    if failed is not None:
        raise HTTPException(status_code=500, detail=str(failed)) from failed
    return reply


def _make_sink(queue: asyncio.Queue, event_loop: asyncio.AbstractEventLoop):
    """
    Build an event sink that pushes into `queue`.

    Most AgentLoop._emit calls fire from the event-loop thread itself (around
    asyncio.to_thread calls, not inside them), so the common path is a direct
    put_nowait. If a future caller emits from a worker thread, we fall back to
    call_soon_threadsafe to stay correct. On QueueFull we drop the oldest
    event to keep the stream bounded.
    """

    def _push(evt: dict) -> None:
        try:
            queue.put_nowait(evt)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # drop oldest
                queue.put_nowait(evt)
            except Exception:
                pass  # give up, keep the producer non-blocking

    def sink(evt: dict) -> None:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is event_loop:
            _push(evt)
        else:
            with contextlib.suppress(RuntimeError):
                event_loop.call_soon_threadsafe(_push, evt)

    return sink


def _get_or_create_session(session_id: str, *, with_sink: bool) -> AgentLoop:
    """
    Idempotent session factory.

    When `with_sink=True` (web UI path), a queue is created on first call and
    a thread-safe sink is wired into the AgentLoop. When False (legacy CLI
    path), no sink is installed and no queue is allocated. First creator wins:
    once a session exists, subsequent calls return it as-is.
    """
    sessions: dict[str, AgentLoop] = app.state.agent_sessions
    if session_id in sessions:
        return sessions[session_id]

    llm = _require_llm()
    blender = _get_client()
    event_sink = None
    if with_sink:
        streams: dict[str, asyncio.Queue] = app.state.agent_streams
        # Reuse the queue created by GET /agent/stream if SSE connected first;
        # only allocate a new one when no subscriber exists yet. Creating a new
        # queue here would overwrite the reference that the SSE generator's
        # closure already holds, silently dropping all events.
        if session_id not in streams:
            streams[session_id] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        queue: asyncio.Queue = streams[session_id]
        event_sink = _make_sink(queue, asyncio.get_running_loop())

    cfg: SessionConfig | None = app.state.session_configs.get(session_id)
    sessions[session_id] = AgentLoop(
        llm=llm,
        blender=blender,
        event_sink=event_sink,
        session_config=cfg.model_dump() if cfg is not None else None,
    )
    return sessions[session_id]


# ── routes ─────────────────────────────────────────────────────────────────


@app.get("/")
async def index(request: Request):
    """Render the chat shell with a fresh session_id (uuid4).

    Issue #9: redirect first-run users (no llm_api_key configured) to /config
    so they can fill provider / key / model before the chat shell loads.
    """
    if not _is_llm_configured():
        return RedirectResponse(url="/config", status_code=307)
    session_id = uuid.uuid4().hex[:12]
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"session_id": session_id},
    )


@app.get("/health")
async def health() -> JSONResponse:
    """
    Liveness + Blender connectivity check.

    200 {"status": "ok", "blender": {...}}  — Blender reachable.
    503 {"detail": "..."}                   — Blender not reachable.
    """
    client = _get_client()
    try:
        scene = client.get_scene_info()
    except (BlenderError, OSError) as exc:
        client.close()  # force reconnect on next request
        raise HTTPException(status_code=503, detail=f"Blender connection lost: {exc}") from exc

    return JSONResponse(
        {
            "status": "ok",
            "blender": {
                "host": settings.blender_host,
                "port": settings.blender_port,
                "scene": scene.get("name"),
                "objects": scene.get("object_count"),
            },
        }
    )


@app.get("/viewport_screenshot")
async def viewport_screenshot(
    max_size: int = Query(800, ge=64, le=2048),
) -> Response:
    """Capture the active 3D viewport and return the PNG bytes.

    Used by the chat shell's side-panel pull loop. Returns 503 (with the
    same Blender hint as /health) when Blender is unreachable, so the
    frontend can render a friendly "Blender not running" placeholder
    instead of a broken image.
    """
    client = _get_client()
    try:
        png = client.get_viewport_screenshot(max_size=max_size)
    except BlenderBusyError as exc:
        # Transient — another caller (long phase tool) holds the socket lock.
        # Do NOT close the client; just signal the frontend to skip this tick.
        raise HTTPException(status_code=503, detail=f"busy: {exc}") from exc
    except (BlenderError, OSError) as exc:
        client.close()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/scene_info")
async def scene_info() -> JSONResponse:
    """
    Proxy Blender's get_scene_info response directly.
    Useful for inspecting the current scene without opening Blender's UI.
    """
    client = _get_client()
    try:
        data = client.get_scene_info()
    except (BlenderError, OSError) as exc:
        client.close()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(data)


# ── /exec: debug only ──────────────────────────────────────────────────────


class ExecRequest(BaseModel):
    code: str


class ExecResponse(BaseModel):
    stdout: str


# ── /agent/chat (legacy, used by cli.py) ───────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    state: str
    session_id: str


@app.post("/agent/chat", response_model=ChatResponse)
async def agent_chat(body: ChatRequest) -> ChatResponse:
    """
    Send a user message to the AgentLoop for the given session.

    Legacy endpoint preserved for cli.py compatibility. No event sink is
    installed on the session created via this route — SSE subscribers will
    see nothing if a session is started here. Use POST /agent/messages
    when the web UI is the entry point.
    """
    loop = _get_or_create_session(body.session_id, with_sink=False)
    reply = await loop.step(body.message)
    return ChatResponse(reply=reply, state=loop.state.value, session_id=body.session_id)


# ── /agent/messages + /agent/stream (Stage 5 web UI) ───────────────────────


@app.post("/agent/messages", response_model=ChatResponse)
async def agent_messages(body: ChatRequest) -> ChatResponse:
    """
    Same JSON shape as /agent/chat, but ensures the session has an event sink
    so SSE subscribers on /agent/stream/{session_id} see live progress. On
    completion, a final `done` event is emitted to close the SSE turn.
    """
    loop = _get_or_create_session(body.session_id, with_sink=True)
    reply = await _run_step_with_done_emit(loop, body.session_id, body.message)
    return ChatResponse(reply=reply, state=loop.state.value, session_id=body.session_id)


# ── /agent/config (Stage 5 issue #3) ───────────────────────────────────────


class SessionConfig(BaseModel):
    """Deterministic parameters collected once per session via the config form.

    Field names here align with the keys expected by phase tools downstream:
      - model_type maps to PoseCorrection.x_preset (MMD / VRChat) or asks once for Other.
      - mod_root surfaces as BatchExport.natives_root.
      - body_parts surfaces as BatchExport.target_parts.
      - use_bone_system surfaces as BatchExport.mhws_use_bonesystem.

    Path existence is checked at the route layer, not here, so we can return
    field-level errors that the UI can pin to specific inputs.
    """

    model_path: str
    # Issue #4: was Literal["MMD", "VRChat", "Other"] in waves 1-4 of stage 5.
    # Now any preset name the live toolkit reports as installed is valid,
    # plus the sentinel "Auto-detect" which tells the pipeline to run
    # InferModelType (setup_infer_model_type) on the imported source rig
    # and back-fill this field via the `model_type_inferred` SSE event.
    # Server-side validation happens in the route handler against
    # app.state.X_PRESETS so newly-supplemented presets work without a
    # SessionConfig schema bump.
    model_type: str = "Auto-detect"
    texture_dir: str

    mod_root: str
    author: str
    character_name: str

    use_bone_system: bool = False
    body_parts: list[Literal["1", "2", "3", "4", "5"]] = Field(..., min_length=1)


class SessionConfigRequest(BaseModel):
    session_id: str
    config: SessionConfig


@app.post("/agent/config")
async def save_session_config(request: Request, body: SessionConfigRequest) -> JSONResponse:
    """
    Save a session's pre-collected parameters server-side.

    Browser file inputs can't surface absolute paths, so the form uses plain
    text inputs and we validate path existence server-side here. Returns 422
    with {field_errors: {name: msg}} so the UI can highlight specific inputs.
    """
    cfg = body.config
    errors: dict[str, str] = {}
    if not Path(cfg.model_path).is_file():
        errors["model_path"] = "File not found"
    if not Path(cfg.texture_dir).is_dir():
        errors["texture_dir"] = "Directory not found"
    if not Path(cfg.mod_root).is_dir():
        errors["mod_root"] = "Directory not found"
    # Issue #4: validate model_type against the runtime X_PRESETS catalog
    # rather than a hardcoded Literal. "Auto-detect" is the sentinel that
    # triggers InferModelType during the pipeline.
    if cfg.model_type != "Auto-detect" and cfg.model_type not in X_PRESETS:
        errors["model_type"] = (
            f"Unknown preset {cfg.model_type!r}. "
            f"Pick one of: Auto-detect, {', '.join(sorted(X_PRESETS))}."
        )
    if errors:
        raise HTTPException(status_code=422, detail={"field_errors": errors})

    request.app.state.session_configs[body.session_id] = cfg
    return JSONResponse({"session_id": body.session_id, "saved": True})


# ── /app/x_presets (Stage 5 issue #4) ──────────────────────────────────────


@app.get("/app/x_presets")
async def get_x_presets() -> JSONResponse:
    """Return the currently-installed X-presets for the session-config form.

    The list is built by the lifespan handler from the toolkit's preset folder
    (with the 13 shipped names as a fallback when Blender isn't reachable at
    startup). Frontend renders one `<option>` per entry plus the leading
    "Auto-detect" sentinel.
    """
    catalog: dict = getattr(app.state, "x_preset_catalog", {}) or {}
    presets = [
        {
            "name": name,
            "slot_count": len(meta.mappings),
            "description": meta.description,
        }
        for name, meta in catalog.items()
    ]
    # Fallback path: catalog is empty (Blender unreachable at boot) → return
    # the shipped names with empty slot/description fields so the dropdown
    # still populates with valid choices.
    if not presets:
        presets = [
            {"name": name, "slot_count": 0, "description": ""}
            for name in sorted(X_PRESETS)
        ]
    presets.sort(key=lambda p: p["name"])
    return JSONResponse({"presets": presets})


# ── /agent/widget (Stage 5 issue #7) ───────────────────────────────────────


class WidgetSubmission(BaseModel):
    """Shared base for widget submissions.

    Both physics-classification and material-mapping forms post flat dotted
    keys (e.g. `type__hair_001`, `texmap__0__hair_mat`) alongside session_id.
    Pydantic v2's `extra="allow"` exposes them via model_extra.
    """

    model_config = ConfigDict(extra="allow")
    session_id: str


def _normalize_value(v: Any) -> str:
    """json-enc may serialize selects as nested arrays for repeated names."""
    if isinstance(v, list):
        return str(v[-1]) if v else ""
    return str(v)


@app.post("/agent/widget/classification")
async def submit_classification_widget(body: WidgetSubmission) -> JSONResponse:
    """
    Receive user confirmations from the Phase 4A classification widget.

    Form posts one `type__<chain_name>` field per chain head. We collect them
    into a {chain_name: inferred_type} dict, JSON-encode it under a
    `[CONFIRMED_CLASSIFICATIONS]` prefix, and feed it to loop.step() so the
    LLM can call physics_chains with chain_classifications directly.
    """
    extras = body.model_extra or {}
    preset_candidates: dict[str, str] = {}
    type_overrides: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    merges: list[str] = []
    for k, v in extras.items():
        if k.startswith("preset__"):
            chain = k[len("preset__"):]
            value = _normalize_value(v).strip()
            if value:
                preset_candidates[chain] = value
        elif k.startswith("type__"):
            chain = k[len("type__"):]
            value = _normalize_value(v).strip()
            if value:
                type_overrides[chain] = value
        elif k.startswith("desc__"):
            chain = k[len("desc__"):]
            value = _normalize_value(v).strip()
            if value:
                descriptions[chain] = value
        elif k.startswith("merge__"):
            chain = k[len("merge__"):]
            # json-enc sends checked checkboxes as boolean true
            if v is True or (isinstance(v, str) and v.lower() in ("true", "on", "1", "yes")):
                merges.append(chain)
    # type__ (explicit override) wins over preset__ (agent accept) for the same chain
    inferred_types = {**preset_candidates, **type_overrides}
    if not inferred_types:
        raise HTTPException(status_code=422, detail="No classification rows submitted.")

    data = {"inferred_types": inferred_types, "descriptions": descriptions, "bones_to_merge": merges}
    formatted = "[CONFIRMED_CLASSIFICATIONS] " + json.dumps(data, ensure_ascii=False)
    loop = _get_or_create_session(body.session_id, with_sink=True)
    await _run_step_with_done_emit(loop, body.session_id, formatted)
    return JSONResponse({"saved": True, "count": len(inferred_types)})


@app.post("/agent/widget/material")
async def submit_material_widget(body: WidgetSubmission) -> JSONResponse:
    """
    Receive user confirmations from the Phase 5 material mapping widget.

    Form posts one `texmap__<slot_idx>__<mat_name>` field per slot per
    material. slot_idx indexes into PRINCIPLED_SLOTS. Empty values mean
    "skip this slot". The result is shaped to match MaterialSetup's
    texture_mapping param ({mat: {slot: path}}).
    """
    extras = body.model_extra or {}
    mapping: dict[str, dict[str, str]] = {}
    for k, v in extras.items():
        if not k.startswith("texmap__"):
            continue
        try:
            _, slot_idx_str, mat_name = k.split("__", 2)
            slot_idx = int(slot_idx_str)
        except (ValueError, IndexError):
            continue
        if slot_idx < 0 or slot_idx >= len(PRINCIPLED_SLOTS):
            continue
        value = _normalize_value(v).strip()
        if not value:
            continue
        slot_name = PRINCIPLED_SLOTS[slot_idx]
        mapping.setdefault(mat_name, {})[slot_name] = value
    if not mapping:
        raise HTTPException(status_code=422, detail="No material slots submitted.")

    formatted = "[CONFIRMED_MATERIAL_MAPPING] " + json.dumps(mapping, ensure_ascii=False)
    loop = _get_or_create_session(body.session_id, with_sink=True)
    await _run_step_with_done_emit(loop, body.session_id, formatted)
    return JSONResponse({"saved": True, "materials": len(mapping)})


# ── /config + /app/config (Stage 5 issue #9) ──────────────────────────────


_API_KEY_MASK = "***"


class AppConfigUpdate(BaseModel):
    """Subset of app.config.Settings editable from the /config UI.

    Fields here mirror app.config_store.PERSISTED_FIELDS. Empty `llm_api_key`
    means "keep the existing key" — the only way to clear a key is to delete
    the persisted JSON file directly.
    """

    llm_provider: Literal["anthropic", "openai_compatible", "ollama"]
    llm_api_key: str = ""
    llm_model: str
    llm_base_url: str = ""
    blender_host: str = "127.0.0.1"
    blender_port: int = 9876


def _refresh_runtime_clients(*, blender_changed: bool) -> dict[str, str]:
    """Re-instantiate app.state.llm + (optionally) app.state.blender after a
    config update. Returns a small status dict shown to the UI."""
    status = {"llm": "unchanged", "blender": "unchanged"}
    try:
        app.state.llm = LLMClient.from_settings()
        status["llm"] = "ok"
    except Exception as exc:
        app.state.llm = None
        status["llm"] = f"error: {exc}"

    if blender_changed:
        old_client: BlenderClient | None = getattr(app.state, "blender", None)
        if old_client is not None:
            with contextlib.suppress(Exception):
                old_client.close()
        new_client = BlenderClient(
            host=settings.blender_host, port=settings.blender_port
        )
        with contextlib.suppress(OSError):
            new_client.connect()
        app.state.blender = new_client
        status["blender"] = "rebound"
    return status


@app.get("/app/config")
async def get_app_config() -> JSONResponse:
    """Return current settings with the API key masked.

    The mask `"***"` signals "a key is set"; an empty string means "no key
    configured yet". The UI uses this distinction to render the placeholder.
    """
    has_key = bool(settings.llm_api_key)
    return JSONResponse({
        "llm_provider": settings.llm_provider,
        "llm_api_key": _API_KEY_MASK if has_key else "",
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "blender_host": settings.blender_host,
        "blender_port": settings.blender_port,
        "has_api_key": has_key,
    })


@app.post("/app/config")
async def post_app_config(body: AppConfigUpdate) -> JSONResponse:
    """Persist user-edited settings and refresh runtime clients.

    Empty `llm_api_key` preserves the existing key — this is what lets the
    UI safely submit the form without re-typing the key on every save.
    Other empty strings overwrite normally (e.g. clearing llm_base_url).
    """
    incoming = body.model_dump()
    if not incoming.get("llm_api_key"):
        # Preserve the existing key. Drop the empty value before persisting
        # so we don't accidentally clobber a previously-saved key.
        incoming.pop("llm_api_key", None)

    blender_changed = (
        incoming.get("blender_host", settings.blender_host) != settings.blender_host
        or incoming.get("blender_port", settings.blender_port) != settings.blender_port
    )

    # Mutate the in-process Settings + persist to disk. Merge with any
    # existing persisted values so a preserved api_key survives the round-trip.
    persisted = load_persisted_config()
    persisted.update(incoming)
    apply_to_settings(settings, persisted)
    save_persisted_config(persisted)

    status = _refresh_runtime_clients(blender_changed=blender_changed)
    return JSONResponse({"saved": True, "status": status})


@app.get("/config")
async def config_page(request: Request):
    """Render the global-config form page."""
    return templates.TemplateResponse(
        request,
        "config.html",
        {"persisted_fields": list(PERSISTED_FIELDS)},
    )


@app.get("/agent/stream/{session_id}")
async def agent_stream(session_id: str, request: Request):
    """
    Long-lived SSE stream of structured events for the given session.

    Each frame uses `event:` = event type (so htmx `sse-swap="phase_started"`
    works directly) and `data:` = full event JSON. A keepalive ping fires
    every 15 s to keep proxies happy.

    If no queue exists yet for this session_id, one is created on connect so
    early subscribers don't race with the POST that creates the AgentLoop.
    """
    streams: dict[str, asyncio.Queue] = app.state.agent_streams
    queue = streams.get(session_id)
    if queue is None:
        queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        streams[session_id] = queue

    # On reconnect, if a loop already exists for this session, re-wire its
    # event_sink to the (possibly new) queue and replay phase progress so the
    # frontend's phase bubbles catch up without requiring a tool call.
    existing_loop = app.state.agent_sessions.get(session_id)
    if existing_loop is not None:
        existing_loop._event_sink = _make_sink(queue, asyncio.get_running_loop())
        existing_loop.emit_phase_sync()

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            if evt["type"] == "error_choice":
                # Ship raw HTML for htmx sse-swap to drop into #error-choice-slot.
                # Issue #6: when the error came from setup_infer_model_type's
                # 0-match path, the renderer adds the [Force Custom] button.
                data = _render_error_choice_html(
                    session_id, category=evt.get("category", "")
                )
            elif evt["type"] == "widget_classification":
                data = _render_classification_widget_html(
                    session_id, evt.get("chains", [])
                )
            elif evt["type"] == "widget_material":
                data = _render_material_widget_html(
                    session_id,
                    evt.get("materials", []),
                    evt.get("existing_connections", {}),
                    evt.get("texture_files", []),
                )
            else:
                data = json.dumps(evt, ensure_ascii=False)
            yield {"event": evt["type"], "data": data}

    return EventSourceResponse(event_generator(), ping=15)


# ── /exec: debug only ──────────────────────────────────────────────────────


if settings.app_debug:

    @app.post("/exec", response_model=ExecResponse)
    async def exec_in_blender(body: ExecRequest) -> ExecResponse:
        """
        Execute arbitrary Python in Blender's main thread and return stdout.

        **DEBUG MODE ONLY** — this route is not registered when APP_DEBUG=false.
        Never expose this endpoint to the public internet.
        """
        client = _get_client()
        try:
            stdout = client.execute(body.code)
        except (BlenderError, OSError) as exc:
            client.close()
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return ExecResponse(stdout=stdout)
