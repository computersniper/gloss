#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BACKEND = path.join(ROOT, "backend");
const FRONTEND = path.join(ROOT, "frontend");
const IS_WINDOWS = process.platform === "win32";
const IS_SOURCE_CHECKOUT = fs.existsSync(path.join(ROOT, ".git"));
const PACKAGE = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));

function runtimeDir() {
  if (process.env.GLOSS_RUNTIME_DIR) return path.resolve(process.env.GLOSS_RUNTIME_DIR);
  if (IS_SOURCE_CHECKOUT) return path.join(BACKEND, ".venv");
  return path.join(cacheDir(), "runtime", `v${PACKAGE.version}`);
}

const VENV_DIR = runtimeDir();
const VENV_PYTHON = path.join(
  VENV_DIR,
  IS_WINDOWS ? "Scripts" : "bin",
  IS_WINDOWS ? "python.exe" : "python",
);
const SETUP_MARKER = path.join(VENV_DIR, ".gloss-ready");

function fail(message) {
  console.error(`Gloss: ${message}`);
  process.exit(1);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? ROOT,
    env: options.env ?? process.env,
    encoding: "utf8",
    stdio: options.capture ? "pipe" : "inherit",
    shell: options.shell ?? false,
  });
  if (result.error) fail(`could not run ${command}: ${result.error.message}`);
  if (result.status !== 0) {
    if (options.capture && result.stderr) process.stderr.write(result.stderr);
    process.exit(result.status ?? 1);
  }
  return result.stdout?.trim() ?? "";
}

function cacheDir() {
  if (process.env.GLOSS_CACHE_DIR) return path.resolve(process.env.GLOSS_CACHE_DIR);
  if (IS_WINDOWS) {
    const local = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
    return path.join(local, "GlossX", "cache");
  }
  return path.join(process.env.XDG_CACHE_HOME || path.join(os.homedir(), ".cache"), "glossx");
}

function setupEnv() {
  const cache = cacheDir();
  const temp = path.join(cache, "tmp");
  fs.mkdirSync(path.join(cache, "pip"), { recursive: true });
  fs.mkdirSync(path.join(cache, "npm"), { recursive: true });
  fs.mkdirSync(temp, { recursive: true });
  return {
    ...process.env,
    PIP_CACHE_DIR: path.join(cache, "pip"),
    npm_config_cache: path.join(cache, "npm"),
    TMPDIR: temp,
    TEMP: temp,
    TMP: temp,
  };
}

function parseOption(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function hasOption(name) {
  return process.argv.includes(name);
}

function requirementsFingerprint() {
  const contents = fs.readFileSync(path.join(BACKEND, "requirements.txt"));
  return crypto.createHash("sha256").update(contents).digest("hex");
}

function environmentPrepared() {
  if (!fs.existsSync(VENV_PYTHON)) return false;
  if (!fs.existsSync(SETUP_MARKER)) return IS_SOURCE_CHECKOUT;
  try {
    const marker = JSON.parse(fs.readFileSync(SETUP_MARKER, "utf8"));
    return marker.requirements === requirementsFingerprint();
  } catch {
    return false;
  }
}

function pythonCandidates() {
  const preferred = parseOption("--python") ?? process.env.GLOSS_PYTHON;
  if (preferred) return [{ command: preferred, prefix: [] }];
  if (IS_WINDOWS) {
    return [
      { command: "py", prefix: ["-3.12"] },
      { command: "py", prefix: ["-3.11"] },
      { command: "python", prefix: [] },
    ];
  }
  return [
    { command: "python3", prefix: [] },
    { command: "python", prefix: [] },
  ];
}

function selectPython() {
  for (const candidate of pythonCandidates()) {
    const probe = spawnSync(
      candidate.command,
      [...candidate.prefix, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
      { encoding: "utf8", shell: false },
    );
    if (probe.status !== 0) continue;
    const [major, minor] = probe.stdout.trim().split(".").map(Number);
    if (major === 3 && minor >= 11) return candidate;
  }
  fail("Python 3.11 or newer was not found. Set GLOSS_PYTHON or pass --python <path>.");
}

function packageManager() {
  const agent = process.env.npm_config_user_agent ?? "";
  return agent.startsWith("bun/") ? "bun" : "npm";
}

function packageCommand(manager) {
  if (!IS_WINDOWS) return manager;
  return manager === "npm" ? "npm.cmd" : "bun.exe";
}

function packageInvocation(manager, args) {
  if (!IS_WINDOWS) return { command: packageCommand(manager), args };
  return {
    command: process.env.ComSpec ?? process.env.COMSPEC ?? "cmd.exe",
    args: ["/d", "/s", "/c", packageCommand(manager), ...args],
  };
}

function runPackage(manager, args, options = {}) {
  const invocation = packageInvocation(manager, args);
  run(invocation.command, invocation.args, options);
}

function setup() {
  const [nodeMajor, nodeMinor] = process.versions.node.split(".").map(Number);
  if (nodeMajor < 18) fail(`Node.js 18 or newer is required (found ${process.versions.node}).`);
  if (IS_SOURCE_CHECKOUT && (nodeMajor < 20 || (nodeMajor === 20 && nodeMinor < 19))) {
    fail(`building from source requires Node.js 20.19 or newer (found ${process.versions.node}).`);
  }
  const env = setupEnv();
  if (!fs.existsSync(VENV_PYTHON)) {
    const python = selectPython();
    console.log("==> creating backend virtual environment");
    run(python.command, [...python.prefix, "-m", "venv", VENV_DIR], { env });
  }
  console.log("==> installing backend dependencies");
  run(VENV_PYTHON, ["-m", "pip", "install", "--upgrade", "pip"], { env });
  run(VENV_PYTHON, ["-m", "pip", "install", "-r", path.join(BACKEND, "requirements.txt")], { env });

  if (IS_SOURCE_CHECKOUT || !fs.existsSync(path.join(FRONTEND, "dist", "index.html"))) {
    if (!fs.existsSync(path.join(FRONTEND, "package.json"))) {
      fail("this package is missing its prebuilt frontend; reinstall Gloss or report a broken release.");
    }
    const manager = packageManager();
    console.log(`==> installing and building frontend with ${manager}`);
    if (manager === "bun") {
      runPackage(manager, ["install", "--frozen-lockfile"], { cwd: FRONTEND, env });
      runPackage(manager, ["run", "build"], { cwd: FRONTEND, env });
    } else {
      runPackage(manager, ["ci", "--no-fund", "--no-audit"], { cwd: FRONTEND, env });
      runPackage(manager, ["run", "build"], { cwd: FRONTEND, env });
    }
  }
  fs.writeFileSync(
    SETUP_MARKER,
    `${JSON.stringify({ version: PACKAGE.version, requirements: requirementsFingerprint() })}\n`,
    "utf8",
  );
  console.log(`==> ready. Cache: ${cacheDir()}`);
  console.log(IS_SOURCE_CHECKOUT
    ? "    Start with: npm start  (or bun run start)"
    : "    Starting Gloss now...");
}

function selectedPort() {
  return Number(parseOption("--port") ?? process.env.GLOSS_PORT ?? "8010");
}

function selectedHost() {
  return parseOption("--host") ?? process.env.GLOSS_HOST ?? "0.0.0.0";
}

function assertPrepared(requireFrontend = true) {
  if (!environmentPrepared()) fail("backend environment is missing or outdated; run npm run setup first.");
  if (requireFrontend && !fs.existsSync(path.join(FRONTEND, "dist", "index.html"))) {
    fail("frontend build is missing; run npm run setup first.");
  }
}

function ensurePrepared() {
  if (!environmentPrepared() || !fs.existsSync(path.join(FRONTEND, "dist", "index.html"))) {
    console.log("Gloss: first run setup (this only happens once per version)");
    setup();
  }
}

function portIsFree(host, port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => server.close(() => resolve(true)));
    server.listen(port, host);
  });
}

async function start() {
  ensurePrepared();
  const host = selectedHost();
  const port = selectedPort();
  if (!(await portIsFree(host, port))) {
    fail(`port ${port} on ${host} is already in use. Set GLOSS_PORT or pass --port <number>.`);
  }
  console.log(`Gloss: http://127.0.0.1:${port}`);
  if (host === "0.0.0.0" || host === "::") {
    console.log("    Phones and tablets: open the GlossX app and use the address and pairing code from Settings > Mobile access.");
  }
  const child = spawn(
    VENV_PYTHON,
    ["-m", "uvicorn", "app.main:app", "--host", host, "--port", String(port)],
    { cwd: BACKEND, env: { ...process.env, GLOSS_BIND_HOST: host, GLOSS_BIND_PORT: String(port) }, stdio: "inherit", shell: false, detached: !IS_WINDOWS },
  );
  supervise([{ child, name: "backend" }]);
}

function doctor() {
  const python = selectPythonOrNull();
  const report = {
    ok: Boolean(python) && fs.existsSync(path.join(FRONTEND, "dist", "index.html")),
    version: PACKAGE.version,
    source_checkout: IS_SOURCE_CHECKOUT,
    node: process.versions.node,
    python: python?.version ?? null,
    prepared: environmentPrepared(),
    frontend_built: fs.existsSync(path.join(FRONTEND, "dist", "index.html")),
    runtime_dir: VENV_DIR,
    cache_dir: cacheDir(),
  };
  if (hasOption("--json")) console.log(JSON.stringify(report, null, 2));
  else {
    console.log(`Gloss ${report.version}`);
    console.log(`Node: ${report.node}`);
    console.log(`Python: ${report.python ?? "missing (3.11+ required for npm installs)"}`);
    console.log(`Ready: ${report.prepared && report.frontend_built ? "yes" : "no"}`);
    console.log(`Runtime: ${report.runtime_dir}`);
  }
  if (!report.ok) process.exitCode = 1;
}

function selectPythonOrNull() {
  for (const candidate of pythonCandidates()) {
    const probe = spawnSync(
      candidate.command,
      [...candidate.prefix, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
      { encoding: "utf8", shell: false },
    );
    if (probe.status !== 0) continue;
    const version = probe.stdout.trim();
    const [major, minor] = version.split(".").map(Number);
    if (major === 3 && minor >= 11) return { ...candidate, version };
  }
  return null;
}

function printHelp() {
  console.log(`Gloss ${PACKAGE.version} - local AI paper reader

Usage:
  gloss-local              install on first run, then start Gloss
  gloss-local start        start the local web app
  gloss-local setup        install or refresh local dependencies
  gloss-local doctor       inspect this installation
  gloss-local dev          run backend and frontend development servers
  gloss-local test         run the project test suite

Options:
  --host <address>          bind address (default: 0.0.0.0)
  --port <number>           service port (default: 8010)
  --python <path>           Python 3.11+ interpreter for first-run setup
  --json                    machine-readable doctor output
  --help                    show this help`);
}

function terminate(child) {
  if (!child || !child.pid || child.exitCode !== null) return;
  if (IS_WINDOWS) {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore" });
  } else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      child.kill("SIGTERM");
    }
  }
}

function supervise(entries) {
  let stopping = false;
  const stop = (code = 0) => {
    if (stopping) return;
    stopping = true;
    for (const { child } of entries) terminate(child);
    process.exit(code);
  };
  process.once("SIGINT", () => stop(130));
  process.once("SIGTERM", () => stop(143));
  process.once("exit", () => {
    for (const { child } of entries) terminate(child);
  });
  for (const { child, name } of entries) {
    child.once("error", (error) => {
      console.error(`Gloss: ${name} failed to start: ${error.message}`);
      stop(1);
    });
    child.once("exit", (code, signal) => {
      if (!stopping) stop(code ?? (signal ? 1 : 0));
    });
  }
}

function dev() {
  assertPrepared(false);
  const port = selectedPort();
  const backend = spawn(
    VENV_PYTHON,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--reload"],
    { cwd: BACKEND, env: process.env, stdio: "inherit", shell: false, detached: !IS_WINDOWS },
  );
  const manager = packageManager();
  const frontendInvocation = packageInvocation(manager, ["run", "dev"]);
  const frontend = spawn(frontendInvocation.command, frontendInvocation.args, {
    cwd: FRONTEND,
    env: process.env,
    stdio: "inherit",
    shell: false,
    detached: !IS_WINDOWS,
  });
  supervise([
    { child: backend, name: "backend" },
    { child: frontend, name: "frontend" },
  ]);
}

function test() {
  assertPrepared(false);
  run(VENV_PYTHON, ["-m", "pip", "install", "-r", path.join(ROOT, "tests", "requirements.txt")]);
  run(VENV_PYTHON, ["-m", "pytest", "-q"], { cwd: ROOT });
  runPackage(packageManager(), ["test"], { cwd: FRONTEND });
  runPackage(packageManager(), ["run", "build"], { cwd: FRONTEND });
}

const command = process.argv[2];
if (command === "--help" || command === "-h" || command === "help") printHelp();
else if (!command || command === "--host" || command === "--port") await start();
else if (command === "setup") setup();
else if (command === "start") await start();
else if (command === "dev") dev();
else if (command === "doctor") doctor();
else if (command === "test") test();
else fail("unknown command. Run gloss-local --help for usage.");
