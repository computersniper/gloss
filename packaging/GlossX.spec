# PyInstaller recipe for the GlossX backend. The Electron shell in desktop/
# ships this folder as resources/backend and starts glossx-backend from it.
from pathlib import Path
import sys


project_root = Path(SPECPATH).parent
windows_icon = project_root / "packaging" / "gloss.ico"

a = Analysis(
    [str(project_root / "packaging" / "windows_launcher.py")],
    pathex=[str(project_root / "backend")],
    binaries=[],
    datas=[
        (str(project_root / "frontend" / "dist"), "frontend/dist"),
        (str(project_root / "packaging" / "gloss.ico"), "packaging"),
        # Built-in GlossX workflow manifests are read from disk at runtime.
        (str(project_root / "backend" / "app" / "agent" / "workflows"), "app/agent/workflows"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        # The CDP client supports both the current and legacy websocket APIs.
        "websockets.asyncio.client",
        "websockets.legacy.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # GlossX does not use packaging/build APIs at runtime. Excluding these also
    # avoids PyInstaller's pkg_resources hook pulling setuptools' optional
    # ``backports`` namespace into the portable executable.
    excludes=[
        "pkg_resources",
        "setuptools",
        "_distutils_hack",
        # The window is Electron's; keep GUI toolkits from the Python
        # installation out of the frozen backend.
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "webview",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="glossx-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # No console window: the desktop shell captures the output itself.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "packaging" / "gloss.ico") if sys.platform == "win32" else None,
)

# A folder rather than one file: the shell starts the backend on every launch,
# and a one-file build would unpack itself to a temporary folder each time.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="glossx-backend",
)
