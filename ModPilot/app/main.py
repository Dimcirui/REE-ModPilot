"""
FastAPI application entry point.

Stage 1 debug endpoints (no agent loop yet):
  GET  /health      — liveness + Blender connectivity check; 503 if Blender unreachable
  GET  /scene_info  — proxy get_scene_info from Blender
  POST /exec        — execute arbitrary Python in Blender (DEBUG mode only)

LLMClient is intentionally absent here; it is wired in Stage 3 (agent loop).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.blender.client import BlenderClient, BlenderError
from app.config import settings

# ── lifespan: manage shared BlenderClient ─────────────────────────────────


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Open one BlenderClient connection for the lifetime of the server.
    Store it on app.state so route handlers can access it.

    If Blender is not running at startup, the server still starts — endpoints
    will return 503 until Blender becomes available.
    """
    client = BlenderClient(host=settings.blender_host, port=settings.blender_port)
    with contextlib.suppress(OSError):
        client.connect()
    app.state.blender = client

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
