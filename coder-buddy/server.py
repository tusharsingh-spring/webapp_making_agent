import asyncio
import io
import json
import queue as _queue
import re
import shutil
import sys
import threading
import traceback
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

import agent.graph as _agent_graph
from agent.graph import agent, refinement_agent, kill_running_app
from agent.tools import PROJECT_ROOT

_agent_graph.HEADLESS = True   # disable input()-based feedback when running via web UI

# ── Shared state ──────────────────────────────────────────────────────────────
_agent_state: dict = {}
_log_raw_q: _queue.Queue = _queue.Queue()
_all_logs: list[str] = []
_is_running = False
_job_status = "idle"   # idle | running | done | error
_open_url = ""
_user_prompt_g = ""


# ── Stdout tee: captures all print() calls from agent threads ─────────────────
class _Tee(io.TextIOBase):
    def __init__(self, orig):
        self._orig = orig

    def write(self, s: str) -> int:
        self._orig.write(s)
        self._orig.flush()
        for line in s.splitlines():
            if line.strip():
                _log_raw_q.put(line)
        return len(s)

    def flush(self):
        self._orig.flush()


# ── Agent runner threads ──────────────────────────────────────────────────────
def _run_generate(user_prompt: str):
    global _agent_state, _is_running, _job_status, _user_prompt_g
    _user_prompt_g = user_prompt
    orig = sys.stdout
    sys.stdout = _Tee(orig)
    try:
        result = agent.invoke(
            {"user_prompt": user_prompt},
            {"recursion_limit": 100},
        )
        _agent_state = result
        _job_status = "done"
    except Exception:
        _job_status = "error"
        traceback.print_exc()
    finally:
        sys.stdout = orig
        _is_running = False
        _log_raw_q.put("__DONE__")


def _run_refine(change_request: str):
    global _agent_state, _is_running, _job_status
    orig = sys.stdout
    sys.stdout = _Tee(orig)
    try:
        result = refinement_agent.invoke(
            {
                "change_request":  change_request,
                "coder_state":     _agent_state.get("coder_state"),
                "user_prompt":     _user_prompt_g,
                "lessons":         _agent_state.get("lessons", ""),
                "design_prompt":   _agent_state.get("design_prompt", ""),
                "enhance":         _agent_state.get("enhance", False),
                "palette":         _agent_state.get("palette", ""),
            },
            {"recursion_limit": 100},
        )
        _agent_state = result
        _job_status = "done"
    except Exception:
        _job_status = "error"
        traceback.print_exc()
    finally:
        sys.stdout = orig
        _is_running = False
        _log_raw_q.put("__DONE__")


# ── Log bridge: sync queue → async broadcast list ─────────────────────────────
async def _log_bridge():
    loop = asyncio.get_running_loop()
    global _open_url
    while True:
        try:
            msg = await loop.run_in_executor(
                None, lambda: _log_raw_q.get(timeout=0.2)
            )
            m = re.search(r"Opening:\s*(https?://\S+)", msg)
            if m:
                _open_url = m.group(1)
            _all_logs.append(msg)
        except _queue.Empty:
            pass
        except Exception:
            await asyncio.sleep(0.1)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    asyncio.create_task(_log_bridge())
    yield
    kill_running_app()


app = FastAPI(lifespan=lifespan)


# ── API endpoints ─────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    prompt: str


class RefineRequest(BaseModel):
    change: str


@app.post("/generate")
async def generate(req: GenerateRequest):
    global _is_running, _job_status, _open_url
    if _is_running:
        return {"ok": False, "msg": "Already running"}

    _all_logs.clear()
    _open_url = ""
    _job_status = "running"
    _is_running = True

    if PROJECT_ROOT.exists():
        shutil.rmtree(PROJECT_ROOT)
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)

    threading.Thread(target=_run_generate, args=(req.prompt,), daemon=True).start()
    return {"ok": True}


@app.post("/refine")
async def refine(req: RefineRequest):
    global _is_running, _job_status, _open_url
    if _is_running:
        return {"ok": False, "msg": "Already running"}
    if not _agent_state.get("coder_state"):
        return {"ok": False, "msg": "No project to refine yet"}

    _all_logs.clear()
    _open_url = ""
    _job_status = "running"
    _is_running = True

    threading.Thread(target=_run_refine, args=(req.change,), daemon=True).start()
    return {"ok": True}


@app.get("/status")
async def status():
    return {
        "status":      _job_status,
        "open_url":    _open_url,
        "has_project": bool(_agent_state.get("coder_state")),
    }


@app.get("/stream")
async def stream(from_idx: int = 0):
    async def generator():
        idx = from_idx
        while True:
            if idx < len(_all_logs):
                msg = _all_logs[idx]
                idx += 1
                yield f"data: {json.dumps({'text': msg})}\n\n"
                if msg == "__DONE__":
                    break
            else:
                await asyncio.sleep(0.05)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return HTMLResponse(_FRONTEND)


_FRONTEND = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Coder Buddy</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0d0d12;
  --surface:#13131f;
  --surface2:#1c1c2e;
  --accent:#7c3aed;
  --accent2:#6d28d9;
  --text:#e2e8f0;
  --dim:#64748b;
  --log:#0a0a10;
  --border:#1e2035;
  --green:#22c55e;
  --yellow:#f59e0b;
  --red:#ef4444;
  --blue:#60a5fa;
  --purple:#a78bfa;
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* ── Header ── */
header{display:flex;align-items:center;justify-content:space-between;padding:14px 24px;background:var(--surface);border-bottom:1px solid var(--border);flex-shrink:0}
.logo{font-size:18px;font-weight:800;letter-spacing:-0.5px}
.logo em{color:var(--purple);font-style:normal}
#status-badge{font-size:12px;padding:3px 12px;border-radius:999px;border:1px solid var(--border);color:var(--dim);background:var(--log);transition:all .3s}
#status-badge.running{color:var(--yellow);border-color:var(--yellow)40}
#status-badge.done{color:var(--green);border-color:var(--green)40}
#status-badge.error{color:var(--red);border-color:var(--red)40}

/* ── Main layout ── */
main{flex:1;display:flex;flex-direction:column;overflow:hidden;padding:20px 24px;gap:14px}

/* ── Hero / build form ── */
#hero{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:28px;transition:all .3s}
#hero h1{font-size:40px;font-weight:900;text-align:center;background:linear-gradient(130deg,var(--purple),var(--blue));-webkit-background-clip:text;-webkit-text-fill-color:transparent;line-height:1.15}
#hero p{color:var(--dim);font-size:15px;text-align:center}
.build-card{width:100%;max-width:620px;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px;display:flex;flex-direction:column;gap:12px}
textarea,input[type=text]{width:100%;background:var(--log);color:var(--text);border:1px solid var(--border);border-radius:9px;padding:13px 15px;font-size:14px;font-family:inherit;resize:none;outline:none;transition:border-color .2s}
textarea:focus,input[type=text]:focus{border-color:var(--accent)}
textarea::placeholder,input::placeholder{color:var(--dim)}
.row{display:flex;justify-content:flex-end;gap:8px}
button.primary{background:var(--accent);color:#fff;border:none;border-radius:9px;padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer;transition:background .2s,transform .1s;display:flex;align-items:center;gap:6px}
button.primary:hover{background:var(--accent2)}
button.primary:active{transform:scale(.97)}
button.primary:disabled{opacity:.45;cursor:not-allowed}
button.ghost{background:transparent;color:var(--dim);border:1px solid var(--border);border-radius:9px;padding:8px 16px;font-size:13px;cursor:pointer;transition:all .2s}
button.ghost:hover{border-color:var(--accent);color:var(--purple)}

/* ── Compact context bar ── */
#ctx-bar{display:none;font-size:13px;color:var(--dim);padding:2px 0;flex-shrink:0}
#ctx-bar.on{display:block}
#ctx-bar strong{color:var(--text)}

/* ── Log panel ── */
#log-wrap{display:none;flex-direction:column;flex:1;gap:8px;overflow:hidden;min-height:0}
#log-wrap.on{display:flex}
.log-toolbar{display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--dim);flex-shrink:0}
#log-panel{flex:1;background:var(--log);border:1px solid var(--border);border-radius:12px;padding:14px 16px;overflow-y:auto;font-family:'Cascadia Code','Fira Code','Consolas',monospace;font-size:12.5px;line-height:1.65;min-height:0}
.ll{white-space:pre-wrap;word-break:break-all;color:#8b949e}
.ll.ok{color:var(--green)}
.ll.warn{color:var(--yellow)}
.ll.err{color:var(--red)}
.ll.info{color:var(--blue)}
.ll.sec{color:var(--purple);font-weight:600}
.ll.step{color:#c9d1d9}

/* ── Refine bar ── */
#refine-bar{display:none;gap:8px;flex-shrink:0}
#refine-bar.on{display:flex}
#refine-input{flex:1}
a.open-app{display:none;align-items:center;gap:5px;background:#22c55e15;color:var(--green);border:1px solid #22c55e30;border-radius:9px;padding:10px 16px;font-size:13px;text-decoration:none;white-space:nowrap;transition:background .2s;flex-shrink:0}
a.open-app:hover{background:#22c55e25}
a.open-app.on{display:flex}

/* ── Spinner ── */
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--yellow)30;border-top-color:var(--yellow);border-radius:50%;animation:sp .65s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<header>
  <div class="logo">⚡ <em>Coder</em>Buddy</div>
  <div id="status-badge">Ready</div>
</header>

<main>
  <div id="ctx-bar"></div>

  <!-- Build form -->
  <div id="hero">
    <div>
      <h1>Build anything<br>with AI</h1>
      <p style="margin-top:10px">Describe your app — Coder Buddy generates the full project end-to-end</p>
    </div>
    <div class="build-card">
      <textarea id="prompt-input" rows="4"
        placeholder="e.g. A Pomodoro timer with dark UI and session history…"
        onkeydown="if(event.ctrlKey&&event.key==='Enter')generate()"></textarea>
      <div class="row">
        <button class="primary" id="gen-btn" onclick="generate()">Generate ⚡</button>
      </div>
    </div>
  </div>

  <!-- Log panel -->
  <div id="log-wrap">
    <div class="log-toolbar">
      <span id="log-label">Logs</span>
      <button class="ghost" onclick="newProject()">+ New project</button>
    </div>
    <div id="log-panel"></div>
  </div>

  <!-- Refine bar -->
  <div id="refine-bar">
    <input type="text" id="refine-input"
      placeholder="Describe any change — UI, logic, features, redesign…"
      onkeydown="if(event.key==='Enter')refine()">
    <button class="primary" id="refine-btn" onclick="refine()">Apply</button>
    <a class="open-app" id="open-btn" href="#" target="_blank">↗ Open App</a>
  </div>
</main>

<script>
let es = null;

const $ = id => document.getElementById(id);

function badge(text, cls) {
  const b = $('status-badge');
  b.textContent = text;
  b.className = cls ? `${cls}` : '';
}

function logLine(text) {
  const p = $('log-panel');
  const d = document.createElement('div');
  d.className = 'll ' + logClass(text);
  d.textContent = text;
  p.appendChild(d);
  p.scrollTop = p.scrollHeight;
}

function logClass(t) {
  if (/✓/.test(t) || /^Wrote|^Patched|^Fixed/.test(t)) return 'ok';
  if (/\[ERROR\]/.test(t)) return 'err';
  if (/\[WARN\]|WARN/.test(t)) return 'warn';
  if (/^={3,}|^━{3,}|^─{3,}/.test(t)) return 'sec';
  if (/^\[|^▶|^Plann|^Archit|^Debug|^Patch|^Review/.test(t)) return 'info';
  return 'step';
}

function startSSE() {
  if (es) es.close();
  es = new EventSource('/stream');
  es.onmessage = e => {
    const {text} = JSON.parse(e.data);
    if (text === '__DONE__') { es.close(); onDone(); return; }

    // capture app URL from log
    const m = text.match(/Opening:\s*(https?:\/\/\S+)/);
    if (m) setOpenUrl(m[1]);

    logLine(text);
  };
  es.onerror = () => { es.close(); badge('Disconnected', 'error'); };
}

function setOpenUrl(url) {
  const btn = $('open-btn');
  btn.href = url;
  btn.classList.add('on');
}

async function generate() {
  const prompt = $('prompt-input').value.trim();
  if (!prompt) return;

  $('hero').style.display = 'none';
  $('log-wrap').classList.add('on');
  $('refine-bar').classList.remove('on');
  $('log-panel').innerHTML = '';
  $('open-btn').classList.remove('on');

  const ctx = $('ctx-bar');
  ctx.innerHTML = `Building: <strong>${esc(prompt)}</strong>`;
  ctx.classList.add('on');
  $('log-label').innerHTML = '<span class="spin"></span> Generating…';

  badge('Running…', 'running');
  $('gen-btn').disabled = true;

  const r = await fetch('/generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({prompt}),
  }).then(r => r.json());

  if (!r.ok) { logLine('[ERROR] ' + r.msg); return; }
  startSSE();
}

async function refine() {
  const change = $('refine-input').value.trim();
  if (!change) return;

  $('log-panel').innerHTML = '';
  $('open-btn').classList.remove('on');
  $('refine-bar').classList.remove('on');
  $('refine-btn').disabled = true;
  $('log-label').innerHTML = '<span class="spin"></span> Applying…';

  const ctx = $('ctx-bar');
  ctx.innerHTML = `Refining: <strong>${esc(change)}</strong>`;

  badge('Running…', 'running');

  const r = await fetch('/refine', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({change}),
  }).then(r => r.json());

  if (!r.ok) { logLine('[ERROR] ' + r.msg); badge('Error', 'error'); $('refine-bar').classList.add('on'); return; }
  $('refine-input').value = '';
  startSSE();
}

function onDone() {
  badge('Done ✓', 'done');
  $('log-label').textContent = 'Logs';
  $('gen-btn').disabled = false;
  $('refine-btn').disabled = false;
  $('refine-bar').classList.add('on');
  $('refine-input').focus();
}

function newProject() {
  if (es) es.close();
  $('hero').style.display = 'flex';
  $('log-wrap').classList.remove('on');
  $('refine-bar').classList.remove('on');
  $('ctx-bar').classList.remove('on');
  $('log-panel').innerHTML = '';
  $('prompt-input').value = '';
  $('gen-btn').disabled = false;
  $('open-btn').classList.remove('on');
  badge('Ready', '');
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("Coder Buddy UI: http://127.0.0.1:3000")
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")
