"""
Unit tests for the global config page + persisted store (issue #9).

Covers:
  - config_store.load() / save() round-trip; unknown keys are dropped;
    corrupt JSON returns {} without crashing.
  - GET /app/config masks the API key as "***" when set, "" when not.
  - POST /app/config with non-empty api_key persists the key; empty api_key
    preserves the previously-stored value (no clobber).
  - POST /app/config refreshes app.state.llm via LLMClient.from_settings.
  - GET / redirects to /config when llm_api_key is empty; renders chat shell
    when set.

Real Blender is not required: BlenderClient is monkey-patched to a no-op stub
and LLMClient.from_settings is replaced with a MagicMock factory. Home dir
is redirected to tmp_path so the test never touches the user's real
~/.modpilot/config.json.

Run with: uv run pytest -m unit tests/unit/test_app_config.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore

from app import config_store
from app.blender.client import BlenderClient
from app.config import Settings
from app.llm.client import LLMClient


def _patch_blender(monkeypatch):
    monkeypatch.setattr(BlenderClient, "connect", lambda self: None)
    monkeypatch.setattr(
        BlenderClient,
        "connected",
        property(lambda self: True),
    )
    monkeypatch.setattr(BlenderClient, "close", lambda self: None)


def _stub_llm(reply_text: str = "ok") -> MagicMock:
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content=reply_text, has_tool_calls=False, tool_calls=[], content_blocks=[],
    )
    return llm


def _patch_llm_factory(monkeypatch, reply_text: str = "ok") -> MagicMock:
    stub = _stub_llm(reply_text)
    monkeypatch.setattr(LLMClient, "from_settings", classmethod(lambda cls: stub))
    return stub


def _redirect_home(monkeypatch, tmp_path: Path) -> None:
    """Send Path.home() into tmp_path so tests don't touch the real ~."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


# ── config_store unit tests ────────────────────────────────────────────────


@pytest.mark.unit
def test_config_store_round_trip(monkeypatch, tmp_path):
    _redirect_home(monkeypatch, tmp_path)
    values = {
        "llm_provider": "openai_compatible",
        "llm_api_key": "sk-test",
        "llm_model": "deepseek-chat",
        "blender_host": "127.0.0.1",
        "blender_port": 9876,
    }
    config_store.save(values)
    loaded = config_store.load()
    assert loaded == values
    assert (tmp_path / ".modpilot" / "config.json").is_file()


@pytest.mark.unit
def test_config_store_drops_unknown_keys(monkeypatch, tmp_path):
    _redirect_home(monkeypatch, tmp_path)
    config_store.save({"llm_provider": "anthropic", "unknown_evil_field": "x"})
    loaded = config_store.load()
    assert "unknown_evil_field" not in loaded
    assert loaded["llm_provider"] == "anthropic"


@pytest.mark.unit
def test_config_store_corrupt_file_returns_empty(monkeypatch, tmp_path):
    _redirect_home(monkeypatch, tmp_path)
    target = tmp_path / ".modpilot" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text("{ not json", encoding="utf-8")
    assert config_store.load() == {}


@pytest.mark.unit
def test_config_store_applies_to_settings(monkeypatch, tmp_path):
    """apply_to_settings mutates a Settings instance in place."""
    _redirect_home(monkeypatch, tmp_path)
    s = Settings()  # picks up whatever's in env
    config_store.apply_to_settings(s, {
        "llm_provider": "anthropic",
        "llm_model": "claude-sonnet-4-5",
        "blender_port": "9999",  # string → coerced to int
    })
    assert s.llm_provider == "anthropic"
    assert s.llm_model == "claude-sonnet-4-5"
    assert s.blender_port == 9999


# ── route tests ───────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_get_app_config_masks_api_key(monkeypatch, tmp_path):
    """When a key is configured, GET /app/config returns '***', not the
    real key. has_api_key is true so the UI can render the placeholder."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.config import settings as runtime_settings
    from app.main import app

    runtime_settings.llm_api_key = "sk-real-secret"
    with TestClient(app) as client:
        r = client.get("/app/config")
        assert r.status_code == 200
        body = r.json()
        assert body["llm_api_key"] == "***"
        assert body["has_api_key"] is True
        assert "sk-real-secret" not in json.dumps(body)


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_get_app_config_signals_empty_key(monkeypatch, tmp_path):
    """When no key is configured, the field is empty + has_api_key is False."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.config import settings as runtime_settings
    from app.main import app

    runtime_settings.llm_api_key = ""
    with TestClient(app) as client:
        r = client.get("/app/config")
        assert r.status_code == 200
        body = r.json()
        assert body["llm_api_key"] == ""
        assert body["has_api_key"] is False


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_app_config_persists_and_refreshes(monkeypatch, tmp_path):
    """Submitting a full payload writes to ~/.modpilot/config.json AND
    rebuilds app.state.llm via LLMClient.from_settings()."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    factory_stub = _patch_llm_factory(monkeypatch)
    from app.config import settings as runtime_settings
    from app.main import app

    with TestClient(app) as client:
        # Confirm the new app.state.llm uses our stubbed factory's output.
        r = client.post("/app/config", json={
            "llm_provider": "openai_compatible",
            "llm_api_key": "sk-new-key",
            "llm_model": "deepseek-chat",
            "llm_base_url": "https://api.deepseek.com/v1",
            "blender_host": "127.0.0.1",
            "blender_port": 9876,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["saved"] is True
        assert body["status"]["llm"] == "ok"

        # Settings singleton mutated
        assert runtime_settings.llm_api_key == "sk-new-key"
        assert runtime_settings.llm_model == "deepseek-chat"

        # JSON file written
        persisted = json.loads((tmp_path / ".modpilot" / "config.json").read_text("utf-8"))
        assert persisted["llm_api_key"] == "sk-new-key"

        # app.state.llm was rebuilt from our stub factory after the save.
        assert app.state.llm is factory_stub


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_app_config_empty_api_key_preserves_existing(monkeypatch, tmp_path):
    """The UI submits the form with an empty api_key field on every save.
    The server MUST keep the existing key in that case — otherwise users
    would lose their key on every settings edit."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.config import settings as runtime_settings
    from app.main import app

    # Seed an existing persisted file + matching in-memory state.
    config_store.save({
        "llm_provider": "openai_compatible",
        "llm_api_key": "sk-original",
        "llm_model": "deepseek-chat",
    })
    runtime_settings.llm_api_key = "sk-original"

    with TestClient(app) as client:
        r = client.post("/app/config", json={
            "llm_provider": "openai_compatible",
            "llm_api_key": "",  # ← empty → keep existing
            "llm_model": "deepseek-reasoner",  # change a different field
            "llm_base_url": "",
            "blender_host": "127.0.0.1",
            "blender_port": 9876,
        })
        assert r.status_code == 200, r.text

        # API key untouched in both settings and the persisted JSON
        assert runtime_settings.llm_api_key == "sk-original"
        persisted = json.loads((tmp_path / ".modpilot" / "config.json").read_text("utf-8"))
        assert persisted["llm_api_key"] == "sk-original"
        # The other field DID change.
        assert persisted["llm_model"] == "deepseek-reasoner"


# ── provider/model guardrail (matching UI presets) ────────────────────────
#
# The /config UI now prefills known-good (model, base_url) per provider, but
# nothing stops a caller from POSTing a mismatched combo (model from one
# provider while llm_provider is another). Catch the obvious traps server-side
# so a stale persisted config can't 404 the agent loop the way it did before.


def _post_config_payload(**overrides) -> dict:
    base = {
        "llm_provider": "openai_compatible",
        "llm_api_key": "sk-test",
        "llm_model": "deepseek-chat",
        "llm_base_url": "https://api.deepseek.com/v1",
        "blender_host": "127.0.0.1",
        "blender_port": 9876,
    }
    base.update(overrides)
    return base


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
@pytest.mark.parametrize(
    "bad_model",
    ["deepseek-chat", "claude-sonnet-4-5", "gpt-4o"],
)
def test_post_app_config_rejects_ollama_with_foreign_model(monkeypatch, tmp_path, bad_model):
    """Ollama Cloud doesn't serve deepseek-chat / Claude / GPT — guardrail
    must return 422 with a field error pointing at llm_model so the UI can
    pin the message to the right input."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/app/config", json=_post_config_payload(
            llm_provider="ollama",
            llm_model=bad_model,
            llm_base_url="",
        ))
        assert r.status_code == 422, r.text
        body = r.json()
        assert "field_errors" in body["detail"]
        assert "llm_model" in body["detail"]["field_errors"]


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_app_config_accepts_ollama_with_native_model(monkeypatch, tmp_path):
    """Ollama with a real Ollama Cloud model (no foreign prefix) saves cleanly."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/app/config", json=_post_config_payload(
            llm_provider="ollama",
            llm_model="deepseek-v4-flash",
            llm_base_url="",
        ))
        assert r.status_code == 200, r.text


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_app_config_rejects_anthropic_with_non_claude_model(monkeypatch, tmp_path):
    """Anthropic endpoint only serves Claude variants — reject anything else."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/app/config", json=_post_config_payload(
            llm_provider="anthropic",
            llm_model="deepseek-chat",
            llm_base_url="",
        ))
        assert r.status_code == 422, r.text
        assert "llm_model" in r.json()["detail"]["field_errors"]


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_app_config_accepts_anthropic_with_claude_model(monkeypatch, tmp_path):
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/app/config", json=_post_config_payload(
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-5",
            llm_base_url="",
        ))
        assert r.status_code == 200, r.text


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
@pytest.mark.parametrize(
    "any_model",
    ["deepseek-chat", "qwen-max", "glm-4-plus", "some-future-model-name"],
)
def test_post_app_config_accepts_openai_compatible_with_any_model(monkeypatch, tmp_path, any_model):
    """openai_compatible is an open universe (DeepSeek, Qwen, ZhipuAI, Mistral, …).
    Any model id is accepted — only the wire format is constrained."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/app/config", json=_post_config_payload(
            llm_provider="openai_compatible",
            llm_model=any_model,
        ))
        assert r.status_code == 200, r.text


# ── existing tests ─────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_root_redirects_to_config_when_unconfigured(monkeypatch, tmp_path):
    """Issue #9 first-run UX: visiting / without a configured LLM redirects
    to /config so the user is funneled to setup."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    monkeypatch.setattr(
        LLMClient, "from_settings",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("missing key"))),
    )
    from app.config import settings as runtime_settings
    from app.main import app

    runtime_settings.llm_api_key = ""
    with TestClient(app) as client:
        # follow_redirects=False so we can assert the 307 + Location.
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "/config"


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_root_serves_spa_when_configured_and_built(monkeypatch, tmp_path):
    """The redirect only fires for first-run users; configured users get the
    Vite-built index.html. When the build is missing we 503 with a build
    hint so dev users know to run `pnpm build`."""
    _redirect_home(monkeypatch, tmp_path)
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.config import settings as runtime_settings
    from app.main import app
    from app import main as main_module

    runtime_settings.llm_api_key = "sk-configured"

    # Build-missing path: 503 with the build-hint detail.
    with TestClient(app) as client:
        r = client.get("/")
        if not main_module._STATIC_BUILT_DIR.joinpath("index.html").is_file():
            assert r.status_code == 503
            assert "pnpm build" in r.json()["detail"]
        else:
            # A real build is present — verify it serves index.html.
            assert r.status_code == 200
            assert "<!doctype html>" in r.text.lower()

    # Synthesize a build by writing a tiny index.html into a tmp dir and
    # pointing _STATIC_BUILT_DIR at it. This exercises the served-SPA path
    # without needing a real Vite run.
    fake_build = tmp_path / "static_built"
    fake_build.mkdir()
    (fake_build / "index.html").write_text(
        "<!doctype html><html><body><div id='root'></div></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_module, "_STATIC_BUILT_DIR", fake_build)
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        assert "id='root'" in r.text or 'id="root"' in r.text
