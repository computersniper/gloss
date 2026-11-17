"""Disposable real backend with a deterministic provider for UI acceptance only.

Usage: backend/.venv/Scripts/python.exe scripts/dev/ui_browser_server.py --port 8798
The normal application never imports this script or enables this provider.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=8798)
parser.add_argument("--data-dir", type=Path, help="Reuse a previously created disposable acceptance directory")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
data = args.data_dir.resolve() if args.data_dir else Path(tempfile.mkdtemp(prefix="glossx-ui-browser-"))
if not data.name.startswith("glossx-ui-browser-"):
    parser.error("--data-dir must be a disposable glossx-ui-browser-* acceptance folder")
os.environ.update(GLOSS_DATA_DIR=str(data / "data"), GLOSS_CACHE_DIR=str(data / "cache"), GLOSSX_HOME=str(data / "home"))
sys.path.insert(0, str(root / "backend"))
from app.main import app
from app.providers import registry
from app.providers.base import Provider, report_stream_usage
from app.config import save_config
from app.harness.settings import save as save_settings
from fastapi.responses import HTMLResponse, Response
import fitz

usage = {"input_tokens": 1000, "cached_input_tokens": 750, "output_tokens": 50, "exact": True}

class Fake(Provider):
    name = "local_codex"
    async def test_connection(self):
        return "UI acceptance provider"
    async def complete(self, system, messages, model=None):
        return "UI acceptance summary.", usage
    async def stream(self, system, messages, model=None):
        report_stream_usage(usage)
        last = str(messages[-1].get("content", "")) if messages else ""
        if "ui screenshot" in last.lower() and not any("browser_screenshot" in str(m.get("content", "")) for m in messages if m.get("role") == "assistant"):
            yield '<tool_call>' + json.dumps({"name": "browser_screenshot", "arguments": {}}) + '</tool_call>'
        else:
            yield "Page reviewed. The shared browser is ready for your next research step."

registry._PROVIDERS["local_codex"] = Fake()
save_config({"provider": "local_codex", "providers": {"local_codex": {"enabled": True}}})
save_settings({"browser": {"allow_private": True}})

@app.get("/ui-acceptance")
def fixture():
    return HTMLResponse('''<!doctype html><title>Shared research page</title><style>body{font:18px system-ui;margin:0;background:#f6f4ef;color:#203145}main{padding:36px 60px;max-width:1000px}h1{font-size:42px;margin:12px 0}input,button{font:18px system-ui;padding:10px}button{background:#345c74;color:white;border:0;border-radius:6px}input{width:300px;border:1px solid #bac4ca;border-radius:6px}p{line-height:1.7}.tag{letter-spacing:3px;color:#597085;font-size:13px}.long{height:1200px}</style><main><div class=tag>GLOSSX · SHARED RESEARCH</div><h1>Read, explore, and make it yours.</h1><p>A live page shared between you and the research agent. Navigate together, collect evidence, and bring useful passages back to the conversation.</p><input aria-label="Research note" placeholder="Write a research note"><button onclick="document.querySelector('#result').textContent='Saved note: '+document.querySelector('input').value">Save note</button><p id=result>No note saved yet.</p><a href="/ui-acceptance.pdf">Download research paper</a><div class=long><h2>Evidence before conclusions</h2><p>Keep sources close to your notes. This page is a deterministic local fixture used only for browser acceptance, with actual DOM input and live CDP frames.</p></div></main>''')
app.router.routes.insert(0, app.router.routes.pop())

@app.get("/ui-acceptance.pdf")
def paper_fixture(title: str = "Shared browser research paper"):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), title, fontsize=18)
        page.insert_text((72, 120), "Abstract: Evidence-driven research and shared browser control.")
        return Response(pdf.tobytes(), media_type="application/pdf")
app.router.routes.insert(0, app.router.routes.pop())
print(json.dumps({"data": str(data), "port": args.port}), flush=True)
import uvicorn
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port))
@app.post("/ui-acceptance/stop")
def stop():
    server.should_exit = True
    return {"stopping": True}
app.router.routes.insert(0, app.router.routes.pop())
server.run()
