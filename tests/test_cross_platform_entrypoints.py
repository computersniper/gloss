from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_top_level_package_exposes_setup_start_and_dev() -> None:
    package_path = ROOT / "package.json"
    assert package_path.is_file(), "top-level package.json is missing"

    scripts = json.loads(package_path.read_text(encoding="utf-8"))["scripts"]
    assert scripts["setup"] == "node scripts/gloss.mjs setup"
    assert scripts["start"] == "node scripts/gloss.mjs start"
    assert scripts["dev"] == "node scripts/gloss.mjs dev"


def test_top_level_package_is_publishable_and_exposes_cli_bins() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["name"] == "gloss-local"
    assert "private" not in package
    assert package["bin"] == {
        "gloss": "scripts/gloss.mjs",
        "gloss-local": "scripts/gloss.mjs",
        "glossx": "scripts/gloss.mjs",
        "glossx-local": "scripts/gloss.mjs",
    }
    assert "frontend/dist/**" in package["files"]
    assert "backend/app/**/*.py" in package["files"]
    assert package["engines"]["node"] == ">=18"


def test_npm_cli_supports_first_run_and_doctor() -> None:
    launcher = (ROOT / "scripts" / "gloss.mjs").read_text(encoding="utf-8")

    assert 'else if (!command || command === "--host" || command === "--port") await start();' in launcher
    assert 'command === "doctor"' in launcher
    assert '".gloss-ready"' in launcher
    assert '"runtime", `v${PACKAGE.version}`' in launcher
    assert "building from source requires Node.js 20.19 or newer" in launcher


def test_windows_executable_packaging_is_wired_into_releases() -> None:
    launcher = ROOT / "packaging" / "windows_launcher.py"
    spec = ROOT / "packaging" / "GlossX.spec"
    icon = ROOT / "packaging" / "gloss.ico"
    build_script = ROOT / "scripts" / "build-windows.ps1"
    workflow = ROOT / ".github" / "workflows" / "release.yml"

    for expected in (launcher, spec, build_script, workflow):
        assert expected.is_file(), f"missing packaging file: {expected.relative_to(ROOT)}"

    config = (ROOT / "backend" / "app" / "config.py").read_text(encoding="utf-8")
    desktop_launcher = launcher.read_text(encoding="utf-8")
    desktop_spec = spec.read_text(encoding="utf-8")
    build_requirements = (ROOT / "packaging" / "requirements-build.txt").read_text(
        encoding="utf-8"
    )
    release = workflow.read_text(encoding="utf-8")
    assert "GLOSS_FRONTEND_DIST" in config
    # The frozen program is only the backend; the Electron shell owns the window.
    assert "import webview" not in desktop_launcher
    assert "GLOSSX_READY_FILE" in desktop_launcher
    assert '"--headless"' in desktop_launcher
    assert "_ensure_standard_streams" in desktop_launcher
    assert "console=False" in desktop_spec
    assert 'name="glossx-backend"' in desktop_spec
    assert "COLLECT(" in desktop_spec
    assert icon.is_file()
    assert "pywebview" not in build_requirements
    assert "./dist/Gloss.exe --version" in release
    assert "npm pack" in release
    assert 'npm publish "gloss-local-$version.tgz" --access public --tag "$dist_tag"' in release
    assert "dist_tag=next" in release
    assert "prerelease:" in release


def test_desktop_shell_ships_the_backend_and_its_browser() -> None:
    desktop = ROOT / "desktop"
    package = json.loads((desktop / "package.json").read_text(encoding="utf-8"))
    main = (desktop / "main.js").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build-windows.ps1").read_text(encoding="utf-8")

    assert package["main"] == "main.js"
    assert {"from": "../dist/glossx-backend", "to": "backend"} in package["build"]["extraResources"]
    assert package["build"]["portable"]["artifactName"] == "Gloss.exe"
    assert (desktop / "package-lock.json").is_file()
    # The backend drives the window's own Chromium and reports where it listens.
    assert "GLOSSX_BROWSER_CDP" in main and "GLOSSX_READY_FILE" in main
    assert "contextIsolation: true" in main and "sandbox: true" in main
    assert "npm --prefix desktop run dist:win" in build_script


def test_windows_build_uses_an_isolated_virtual_environment() -> None:
    script = (ROOT / "scripts" / "build-windows.ps1").read_text(encoding="utf-8")

    assert '"build\\windows-venv"' in script
    assert "& $buildPython -m pip install" in script
    assert "& $buildPython -m PyInstaller" in script


def test_windows_launcher_selects_the_next_available_port(monkeypatch) -> None:
    launcher_path = ROOT / "packaging" / "windows_launcher.py"
    spec = importlib.util.spec_from_file_location("gloss_windows_launcher", launcher_path)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    monkeypatch.setattr(
        launcher,
        "_port_available",
        lambda _host, port: port == 8012,
    )

    assert launcher._select_available_port("127.0.0.1", 8010) == 8012


def test_launcher_reports_its_address_to_the_desktop_shell(monkeypatch, tmp_path) -> None:
    launcher_path = ROOT / "packaging" / "windows_launcher.py"
    spec = importlib.util.spec_from_file_location("gloss_windows_ready", launcher_path)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    ready = tmp_path / "ready"
    monkeypatch.setenv("GLOSSX_READY_FILE", str(ready))
    launcher._report_ready("http://127.0.0.1:8012")
    assert ready.read_text(encoding="utf-8") == "http://127.0.0.1:8012"

    monkeypatch.delenv("GLOSSX_READY_FILE")
    launcher._report_ready("http://127.0.0.1:8013")
    assert ready.read_text(encoding="utf-8") == "http://127.0.0.1:8012"


def test_native_linux_and_windows_wrappers_exist() -> None:
    expected = [
        "scripts/setup.sh",
        "scripts/run.sh",
        "scripts/dev.sh",
        "scripts/setup.ps1",
        "scripts/run.ps1",
        "scripts/dev.ps1",
        "gloss",
        "gloss.ps1",
    ]
    for relative in expected:
        assert (ROOT / relative).is_file(), f"missing native wrapper: {relative}"


def test_linux_setup_no_longer_contains_machine_specific_data_disk() -> None:
    setup = (ROOT / "scripts" / "setup.sh").read_text(encoding="utf-8")
    assert "/root/autodl-tmp" not in setup
    assert 'gloss.mjs" setup' in setup


def test_tagline_mentions_multiple_provider_choices() -> None:
    library = (ROOT / "frontend" / "src" / "components" / "Library.tsx").read_text(
        encoding="utf-8"
    )
    assert "powered by Claude, Codex, or your preferred AI provider" in library
    assert "由 Claude、Codex 或你选择的 AI 提供方驱动" in library


def test_url_import_status_explains_that_network_import_can_take_time() -> None:
    library = (ROOT / "frontend" / "src" / "components" / "Library.tsx").read_text(
        encoding="utf-8"
    )
    assert "Downloading and parsing" in library
    assert "正在下载并解析" in library


def test_launcher_treats_empty_cache_environment_values_as_unset() -> None:
    launcher = (ROOT / "scripts" / "gloss.mjs").read_text(encoding="utf-8")
    assert "process.env.LOCALAPPDATA ||" in launcher
    assert "process.env.XDG_CACHE_HOME ||" in launcher


def test_normal_test_commands_include_frontend_regressions() -> None:
    frontend_scripts = json.loads(
        (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )["scripts"]
    launcher = (ROOT / "scripts" / "gloss.mjs").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert frontend_scripts["test"] == "node --test tests/*.test.mjs"
    assert 'runPackage(packageManager(), ["test"], { cwd: FRONTEND })' in launcher
    assert "npm test" in workflow


def test_top_bar_provider_switcher_exposes_status_and_brand_marks() -> None:
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    switcher = (ROOT / "frontend" / "src" / "components" / "ProviderSwitcher.tsx").read_text(
        encoding="utf-8"
    )
    styles = (ROOT / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert "<ProviderSwitcher />" in app
    assert 'local_codex: { label: "Codex", kind: "openai" }' in switcher
    assert 'local_claude: { label: "Claude", kind: "anthropic" }' in switcher
    assert 'openai: { label: "Local API", kind: "local" }' in switcher
    assert "provider-status-dot" in switcher
    assert ".provider-status-dot.connected" in styles
    assert ".provider-status-dot.checking" in styles


def test_desktop_exit_dialog_warns_about_in_flight_work() -> None:
    dialog = (ROOT / "frontend" / "src" / "components" / "ExitConfirmDialog.tsx").read_text(
        encoding="utf-8"
    )
    settings = (ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text(
        encoding="utf-8"
    )

    assert "正在导入的任务会被取消" in dialog
    assert "Summary、Mind Map、Chat" in dialog
    assert "下次不再提示" in dialog
    assert "confirm_exit" in settings
