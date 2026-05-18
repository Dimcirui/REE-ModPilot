# Pyinstaller spec for the ModPilot FastAPI backend sidecar.
#
# Build:
#   .venv\Scripts\pyinstaller.exe modpilot_backend.spec --clean --noconfirm
#
# Output:
#   dist/modpilot-backend/modpilot-backend.exe (one-folder; ~50 MB)
#
# One-folder over one-file: --onefile re-extracts the bundle to a temp dir
# on every launch (~1-2 s startup latency, plus AV scanners flag the
# transient extraction). One-folder boots immediately.

from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from pathlib import Path

REPO_ROOT = Path.cwd().parent  # spec is in ModPilot/; parent is repo root

block_cipher = None

# Hidden imports —
#   - uvicorn[standard] pulls these in at runtime via lazy dispatch; pyinstaller
#     can't see them from static analysis. Include the lot so http/ws variants
#     all work.
#   - anthropic / openai / sse_starlette / pydantic / pydantic_settings have
#     submodules referenced via string lookups in their own internals.
hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("anthropic")
    + collect_submodules("openai")
    + collect_submodules("sse_starlette")
    + collect_submodules("pydantic")
    + collect_submodules("pydantic_settings")
    + collect_submodules("httpx")
    + collect_submodules("httpcore")
    + collect_submodules("h11")
    + ["httptools", "websockets", "watchfiles", "uvloop"]  # uvicorn[standard] extras
)

# Data files —
#   - Bundle the catalogs that app/data/ holds + the workflow doc that
#     app/agent/prompts.py reads at import time + the Vite SPA bundle.
#   - The (source, dest_within_bundle) tuples mirror the layout
#     app/resources.py expects under sys._MEIPASS.
datas = [
    (str(REPO_ROOT / "ModPilot" / "app" / "data"), "app/data"),
    (str(REPO_ROOT / "docs" / "agent_workflow.md"), "docs"),
    (str(REPO_ROOT / "ModPilot" / "app" / "static_built"), "app/static_built"),
]
# Plus any data files anthropic / openai SDKs ship (e.g. cacerts, tokenizers).
datas += collect_data_files("anthropic")
datas += collect_data_files("openai")
datas += collect_data_files("certifi")

a = Analysis(
    ["modpilot_backend.py"],
    pathex=[str(REPO_ROOT / "ModPilot")],  # so `from app.main import app` resolves
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Test-only deps that drag in playwright / pytest if not pruned.
        "pytest",
        "playwright",
        "_pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="modpilot-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # show logs in dev; the Tauri parent reads stdout
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="modpilot-backend",
)
