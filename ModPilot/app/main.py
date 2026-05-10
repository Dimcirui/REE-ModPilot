"""
FastAPI application entry point.

Debug endpoints (Stage 1):
  GET  /health      — liveness + Blender connectivity check; 503 if Blender unreachable
  GET  /scene_info  — proxy get_scene_info from Blender
  POST /exec        — execute arbitrary Python in Blender (DEBUG mode only)

Agent endpoint (Stage 3):
  POST /agent/chat  — send a user message to the AgentLoop; returns agent reply + state
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent.loop import AgentLoop
from app.blender.client import BlenderClient, BlenderError
from app.config import settings
from app.llm.client import LLMClient

# ── lifespan: manage shared BlenderClient ─────────────────────────────────


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Open one BlenderClient connection and one LLMClient for the server lifetime.
    Both are stored on app.state for route handler access.

    If Blender is not running at startup, the server still starts — endpoints
    will return 503 until Blender becomes available.
    """
    client = BlenderClient(host=settings.blender_host, port=settings.blender_port)
    with contextlib.suppress(OSError):
        client.connect()
    app.state.blender = client
    app.state.llm = LLMClient.from_settings()
    app.state.agent_sessions: dict[str, AgentLoop] = {}

    yield

    client.close()


# ── app factory ────────────────────────────────────────────────────────────

app = FastAPI(
    title="ModPilot",
    description="AI-guided Blender automation for RE Engine character mods",
    version="0.1.0",
    lifespan=lifespan,
)


# ── helpers ────────────────────────────────────────────────────────────────

_BLENDER_HINT = (
    "Blender is not reachable. "
    "Start Blender, enable the blender-mcp addon, and click 'Connect to Claude'."
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


# ── routes ─────────────────────────────────────────────────────────────────


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


# ── /agent/chat ────────────────────────────────────────────────────────────


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

    A new AgentLoop is created on the first message for a session_id.
    Sessions are in-memory only; they reset on server restart.
    """
    sessions: dict[str, AgentLoop] = app.state.agent_sessions
    if body.session_id not in sessions:
        blender: BlenderClient = app.state.blender
        if not blender.connected:
            try:
                blender.connect()
            except OSError as exc:
                raise HTTPException(status_code=503, detail=_BLENDER_HINT) from exc
        sessions[body.session_id] = AgentLoop(
            llm=app.state.llm,
            blender=blender,
        )

    loop = sessions[body.session_id]
    reply = await loop.step(body.message)
    return ChatResponse(reply=reply, state=loop.state.value, session_id=body.session_id)


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
