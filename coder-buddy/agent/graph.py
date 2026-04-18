import json as _json
import re
import subprocess
import threading
import time
import webbrowser
from pydantic import BaseModel, Field


def _parse_retry_after(msg: str) -> float:
    """Extract seconds from Groq's 'Please try again in Xm Y.Zs' message."""
    m = re.search(r"try again in\s+(?:(\d+)m\s*)?(\d+(?:\.\d+)?)s", msg)
    if m:
        minutes = int(m.group(1) or 0)
        seconds = float(m.group(2))
        return minutes * 60 + seconds + 2  # +2s buffer
    return 10.0  # fallback


def _llm_invoke_with_retry(chain, prompt: str, retries: int = 4):
    """Invoke an LLM chain, retrying on rate-limit AND parse/validation errors."""
    for attempt in range(retries):
        try:
            return chain.invoke(prompt)
        except Exception as e:
            msg = str(e)
            if "rate_limit_exceeded" in msg or "429" in msg:
                wait = _parse_retry_after(msg)
                print(f"\n[Rate limit] Waiting {wait:.0f}s (retry {attempt + 1}/{retries})…")
                time.sleep(wait)
            elif "OutputParserException" in type(e).__name__ or "ValidationError" in type(e).__name__:
                if attempt < retries - 1:
                    print(f"\n[Parse error] Retrying ({attempt + 1}/{retries})…")
                    time.sleep(1)
                else:
                    raise
            else:
                raise
    raise RuntimeError("LLM retries exhausted.")

from dotenv import load_dotenv
from langchain.globals import set_verbose, set_debug
from langchain_groq.chat_models import ChatGroq
from langgraph.constants import END
from langgraph.graph import StateGraph

from agent.feedback import FeedbackStore, collect_feedback
from agent.prompts import (
    planner_prompt, architect_prompt, coder_system_prompt,
    executor_prompt, patch_planner_prompt, reviewer_prompt,
)
from agent.states import Plan, TaskPlan, ImplementationTask, CoderState, PatchPlan
from agent.tools import write_file, read_file, list_files, init_project_root, PROJECT_ROOT
from agent.tuner import load_rules, format_rules_block
from resources.design_system import is_enhance_request, get_design_prompt, pick_palette

_ = load_dotenv()
set_debug(False)
set_verbose(False)

llm       = ChatGroq(model="llama-3.1-8b-instant")       # fast: planner, reviewer
llm_smart = ChatGroq(model="llama-3.3-70b-versatile")   # smart: architect, coder, patch
store = FeedbackStore()

# Set to True when running via the web server so input()-based prompts are skipped
HEADLESS = False


def _load_agent_rules() -> tuple[str, str, str]:
    """Returns (planner_rules_str, architect_rules_str, coder_rules_str)."""
    rules = load_rules()
    return (
        format_rules_block(rules.planner_rules,   "PLANNER"),
        format_rules_block(rules.architect_rules, "ARCHITECT"),
        format_rules_block(rules.coder_rules,     "CODER"),
    )


def _extract_code_block(text: str, ext: str = "") -> str:
    """Pull content out of the first code block in an LLM response."""
    # Try ```ext ... ```
    if ext:
        m = re.search(rf'```{re.escape(ext)}\s*\n?([\s\S]+?)```', text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    # Try ``` ... ``` (any language tag or none)
    m = re.search(r'```[^\n]*\n([\s\S]+?)```', text)
    if m:
        return m.group(1).strip()
    # No fences — return everything (model skipped the block)
    return text.strip()


def _invoke_file_content(prompt: str, filepath: str, llm_instance=None) -> str | None:
    """Raw LLM call — ask for file content inside a code block, extract via regex.

    Code blocks are far more reliable than json_mode for weak models: no JSON
    escaping, no 'content' key confusion, and models are trained to use them.
    """
    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
    model = llm_instance or llm
    full_prompt = (
        prompt
        + f"\n\nReturn ONLY the complete, final content of `{filepath}` "
        + f"inside a single ```{ext} ... ``` code block. "
        + "No explanation. No text outside the code block."
    )
    response = _llm_invoke_with_retry(model, full_prompt)
    text = response.content if hasattr(response, "content") else str(response)
    content = _extract_code_block(text, ext)
    return content if content else None


class RunScript(BaseModel):
    setup_commands: list[str] = Field(description="Commands to install dependencies")
    run_command: str = Field(description="Single command to start the project")
    open_url: str = Field(default="", description="URL to open in browser, or empty")
    notes: str = Field(description="One sentence on what the user will see")


# ── Helpers ───────────────────────────────────────────────────────────────────

_running_proc: "subprocess.Popen | None" = None


def kill_running_app() -> None:
    """Terminate the currently running app process, if any."""
    global _running_proc
    if _running_proc and _running_proc.poll() is None:
        _running_proc.terminate()
        try:
            _running_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _running_proc.kill()
    _running_proc = None


def _design_for(prompt: str) -> tuple[bool, str, str]:
    """Returns (enhance, palette_name, design_prompt_string)."""
    if is_enhance_request(prompt):
        palette = pick_palette(prompt)
        return True, palette, get_design_prompt(palette)
    return False, "", ""


def _free_port(port: int) -> None:
    """Kill any process occupying the given port on Windows (best-effort)."""
    try:
        result = subprocess.run(
            f'for /f "tokens=5" %a in (\'netstat -aon ^| findstr ":{port}"\') do taskkill /F /PID %a',
            shell=True, capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _run_executor(plan: Plan, coder_state: CoderState) -> dict:
    """Launch the app non-blocking: stream boot logs, open browser, return immediately."""
    global _running_proc

    # Kill any previously running app then free the port to avoid ERR_EMPTY_RESPONSE
    kill_running_app()
    _free_port(8080)

    files = list_files.run(".")
    prompt = (
        f"{executor_prompt()}\n\n"
        f"Project: {plan.name}\nTech stack: {plan.techstack}\n"
        f"Features: {', '.join(plan.features)}\nFiles:\n{files}\n\n"
        'Return JSON: {"setup_commands": [...], "run_command": "...", "open_url": "...", "notes": "..."}'
    )
    resp = _llm_invoke_with_retry(llm.with_structured_output(RunScript, method="json_mode"), prompt)
    if resp is None:
        return {}

    # Write helper scripts
    bat = ["@echo off", "cd /d %~dp0"] + resp.setup_commands + [resp.run_command]
    if resp.open_url:
        bat.append(f'start "" "{resp.open_url}"')
    write_file.run({"path": "run.bat", "content": "\n".join(bat)})

    sh = ["#!/bin/bash", 'cd "$(dirname "$0")"'] + resp.setup_commands + [resp.run_command]
    if resp.open_url:
        sh.append(f'open "{resp.open_url}" 2>/dev/null || xdg-open "{resp.open_url}" 2>/dev/null || true')
    write_file.run({"path": "run.sh", "content": "\n".join(sh)})

    md = (
        f"# How to Run — {plan.name}\n\n**Stack:** {plan.techstack}\n\n"
        f"## Setup\n```\n{chr(10).join(resp.setup_commands) or '(none)'}\n```\n\n"
        f"## Run\n```\n{resp.run_command}\n```\n\n"
        + (f"## Open\n{resp.open_url}\n\n" if resp.open_url else "")
        + f"## Notes\n{resp.notes}\n\n"
        "## Windows\n```\nrun.bat\n```\n\n## Linux/Mac\n```\nbash run.sh\n```\n"
    )
    write_file.run({"path": "HOW_TO_RUN.md", "content": md})

    # Drop Windows-incompatible, Unix-only, or file-referencing commands where the
    # file doesn't actually exist in the project directory.
    _BAD_PREFIX = ("chmod", "npm install", "npm run", "yarn", "brew ", "apt ", "sudo ",
                   "pip install -r", "pip3 install -r")
    def _cmd_ok(cmd: str) -> bool:
        c = cmd.strip()
        if any(c.startswith(b) for b in _BAD_PREFIX):
            return False
        # reject any "X -r <file>" style install where <file> is missing
        import re as _re
        m = _re.search(r"-r\s+(\S+)", c)
        if m and not (PROJECT_ROOT / m.group(1)).exists():
            return False
        return True
    resp.setup_commands = [c for c in resp.setup_commands if _cmd_ok(c)]

    # ── Setup ────────────────────────────────────────────────────────────────
    if resp.setup_commands:
        print(f"\n{'='*50}\nSETUP\n{'='*50}")
        for cmd in resp.setup_commands:
            print(f"\n$ {cmd}")
            try:
                r = subprocess.run(cmd, shell=True, cwd=str(PROJECT_ROOT),
                                   capture_output=True, text=True, timeout=120)
                if r.stdout.strip():
                    print(r.stdout.strip()[:1000])
                if r.returncode != 0:
                    print(f"[ERROR] '{cmd}' failed (exit {r.returncode})\n{r.stderr.strip()[:800]}")
                    return {"launch_failed": True}
                print("  ✓ done")
            except subprocess.TimeoutExpired:
                print(f"[ERROR] '{cmd}' timed out"); return {"launch_failed": True}
            except Exception as e:
                print(f"[ERROR] {e}"); return {"launch_failed": True}

    # ── Launch (non-blocking) ─────────────────────────────────────────────────
    print(f"\n{'='*50}\nLAUNCHING: {resp.run_command}\n{'='*50}")
    try:
        proc = subprocess.Popen(resp.run_command, shell=True, cwd=str(PROJECT_ROOT),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        _running_proc = proc
        boot_log = []
        deadline = time.time() + 5

        def stream():
            for line in proc.stdout:
                boot_log.append(line.rstrip())
                print(f"  {line.rstrip()}")
                if time.time() > deadline:
                    break

        t = threading.Thread(target=stream, daemon=True)
        t.start(); t.join(timeout=6)
        time.sleep(0.3)

        if proc.poll() not in (None, 0):
            print(f"\n[ERROR] Process crashed (exit {proc.poll()}). Check startup_log.txt.")
            write_file.run({"path": "startup_log.txt", "content": "\n".join(boot_log)})
            _running_proc = None
            return {"launch_failed": True}

        print(f"\n✓ App is running (PID {proc.pid})")
        if resp.open_url:
            time.sleep(0.5)
            print(f"  Opening: {resp.open_url}")
            webbrowser.open(resp.open_url)
        write_file.run({"path": "startup_log.txt", "content": "\n".join(boot_log)})

    except Exception as e:
        print(f"[ERROR] {e}")

    return {}


# ── Main graph agents ─────────────────────────────────────────────────────────

def planner_agent(state: dict) -> dict:
    user_prompt: str = state["user_prompt"]
    enhance, palette, design_prompt = _design_for(user_prompt)

    p_rules, a_rules, c_rules = _load_agent_rules()
    lessons = store.get_lessons(techstack="", limit=3)

    if enhance:
        print(f"[Design] Enhance mode ON — palette: {palette}")
    if p_rules:
        print(f"[Tuner] Planner rules loaded ({p_rules.count(chr(10))-1} rules).")
    if lessons:
        print(f"[Feedback] {store.total()} past entries loaded.")

    resp = _llm_invoke_with_retry(llm_smart.with_structured_output(Plan, method="json_mode"),
        planner_prompt(user_prompt, lessons=lessons, rules=p_rules)
    )
    if resp is None or not resp.files:
        raise ValueError("Planner returned nothing or no valid files.")
    return {"plan": resp, "lessons": lessons, "enhance": enhance,
            "design_prompt": design_prompt, "palette": palette,
            "arch_rules": a_rules, "coder_rules": c_rules}


def architect_agent(state: dict) -> dict:
    plan: Plan = state["plan"]
    user_prompt: str = state.get("user_prompt", "")
    a_rules: str = state.get("arch_rules", "")
    lessons: str = store.get_lessons(techstack=plan.techstack, limit=3)

    resp = _llm_invoke_with_retry(
        llm_smart.with_structured_output(TaskPlan, method="json_mode"),
        architect_prompt(plan.model_dump_json(), user_prompt=user_prompt,
                         lessons=lessons, rules=a_rules),
    )
    if resp is None:
        raise ValueError("Architect returned nothing.")
    resp.plan = plan
    # Deduplicate: if same filepath appears multiple times, merge into one task
    seen: dict[str, str] = {}
    for step in resp.implementation_steps:
        if step.filepath in seen:
            seen[step.filepath] += "\n" + step.task_description
        else:
            seen[step.filepath] = step.task_description
    resp.implementation_steps = [
        ImplementationTask(filepath=fp, task_description=desc)
        for fp, desc in seen.items()
    ]
    print(resp.model_dump_json().encode("utf-8", errors="replace").decode("utf-8"))
    return {"task_plan": resp, "lessons": lessons}


def coder_agent(state: dict) -> dict:
    coder_state: CoderState = state.get("coder_state")
    lessons: str = state.get("lessons", "")
    design_prompt: str = state.get("design_prompt", "")
    user_prompt: str = state.get("user_prompt", "")
    c_rules: str = state.get("coder_rules", "")

    if coder_state is None:
        init_project_root()
        coder_state = CoderState(task_plan=state["task_plan"], current_step_idx=0)

    steps = coder_state.task_plan.implementation_steps
    if coder_state.current_step_idx >= len(steps):
        return {"coder_state": coder_state, "status": "DONE"}

    current_task = steps[coder_state.current_step_idx]
    existing_content = read_file.run(current_task.filepath)
    plan: Plan = coder_state.task_plan.plan

    # Build cross-file context: show FULL content of already-written files so
    # JS can see exactly which classes/IDs/data-* exist in HTML before referencing them.
    all_files = [f.path for f in plan.files]
    written_ctx_parts = []
    for f in plan.files:
        if f.path == current_task.filepath:
            continue
        content = read_file.run(f.path)
        if content:
            written_ctx_parts.append(f"=== {f.path} (already written — FULL CONTENT) ===\n{content}")
    written_ctx = ("\n\n" + "\n\n".join(written_ctx_parts)) if written_ctx_parts else ""

    # Derive explicit link instructions from the filepath
    ext = current_task.filepath.rsplit(".", 1)[-1].lower()
    css_files  = [p for p in all_files if p.endswith(".css")]
    js_files   = [p for p in all_files if p.endswith(".js")]
    link_hint  = ""
    if ext == "html":
        links = [f'<link rel="stylesheet" href="{c}">' for c in css_files]
        links += [f'<script src="{j}" defer></script>' for j in js_files]
        link_hint = (
            "\nLINKING (include ALL of these in <head>):\n"
            + "\n".join(f"  {l}" for l in links)
            + "\n"
        )

    change_request: str = state.get("change_request", "")
    change_ctx = (
        f"\nREFINEMENT REQUEST: {change_request}\n"
        "Apply this change while preserving everything else.\n"
    ) if change_request else ""

    prompt = (
        f"{coder_system_prompt(plan, user_prompt=user_prompt, lessons=lessons, design_prompt=design_prompt, rules=c_rules)}\n\n"
        f"Project files: {', '.join(all_files)}\n"
        f"{link_hint}"
        f"{change_ctx}"
        f"Task: {current_task.task_description}\n"
        f"File to write: {current_task.filepath}\n"
        f"Existing content:\n{existing_content or '(empty)'}\n"
        f"{written_ctx}"
    )

    # Use smart model during refinement — 8b can't reason about logic bugs
    coder_model = llm_smart if change_request else llm
    content = _invoke_file_content(prompt, current_task.filepath, coder_model)
    if content and content.strip():
        write_file.run({"path": current_task.filepath, "content": content})
        print(f"Wrote: {current_task.filepath}")
    else:
        print(f"[WARN] Coder returned empty content for {current_task.filepath} — skipping write.")

    coder_state.current_step_idx += 1
    return {"coder_state": coder_state}


def reviewer_agent(state: dict) -> dict:
    """Reads all files, finds cross-file bugs, fixes them inline. No loop back to coder."""
    coder_state: CoderState = state["coder_state"]
    plan: Plan = coder_state.task_plan.plan

    # Read all project files
    file_contents: dict[str, str] = {}
    for f in plan.files:
        if f.path in file_contents:
            continue
        fc = read_file.run(f.path)
        if fc:
            file_contents[f.path] = fc

    if not file_contents:
        return {"status": "DONE", "coder_state": coder_state}

    files_block = "\n\n".join(
        f"--- {fp} ({len(c)} chars) ---\n{c}" for fp, c in file_contents.items()
    )

    class ReviewResult(BaseModel):
        has_issues: bool = Field(description="True only for bugs that break the app")
        issues: list[dict] = Field(
            default_factory=list,
            description='[{"filepath":"...","problem":"...","fix":"exact fix instructions"}]'
        )
        summary: str = Field(description="One-line verdict")

    try:
        resp = _llm_invoke_with_retry(
            llm.with_structured_output(ReviewResult, method="json_mode"),
            reviewer_prompt(plan.name, plan.techstack, files_block)
        )
    except Exception:
        resp = None

    if resp is None or not resp.has_issues:
        print(f"[Reviewer] ✓ {resp.summary if resp else 'Looks good.'}")
        return {"status": "DONE", "coder_state": coder_state}

    print(f"[Reviewer] Fixing inline: {resp.summary}")

    # Group fixes by file, then fix each file once with a single LLM call
    fixes_by_file: dict[str, list[str]] = {}
    for issue in resp.issues:
        fp = issue.get("filepath", "")
        fix = issue.get("fix") or issue.get("problem") or ""
        prob = issue.get("problem", fix)
        if fp and fix:
            fixes_by_file.setdefault(fp, []).append(fix)
            print(f"  • {fp}: {prob}")

    for fp, fixes in fixes_by_file.items():
        other_files = "\n\n".join(
            f"=== {p} ===\n{c}" for p, c in file_contents.items() if p != fp
        )
        fix_prompt = (
            f"Project: {plan.name} ({plan.techstack})\n\n"
            f"Other files for reference:\n{other_files}\n\n"
            f"File to fix: {fp}\n"
            f"Current content:\n{file_contents.get(fp, '')}\n\n"
            f"Fix these issues:\n" + "\n".join(f"- {f}" for f in fixes)
        )
        fixed = _invoke_file_content(fix_prompt, fp, llm_smart)
        if fixed and fixed.strip():
            write_file.run({"path": fp, "content": fixed})
            file_contents[fp] = fixed  # keep context fresh for next file
            print(f"  ✓ Fixed: {fp}")

    return {"status": "DONE", "coder_state": coder_state}


def executor_agent(state: dict) -> dict:
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        print("[Executor] No project state — skipping launch.")
        return {"coder_state": coder_state}
    result = _run_executor(coder_state.task_plan.plan, coder_state)
    # Always carry coder_state forward so refinement can access it
    return {"coder_state": coder_state, **result}


def feedback_agent(state: dict) -> dict:
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        return {"coder_state": coder_state}
    if HEADLESS:
        return {"coder_state": coder_state}
    plan: Plan = coder_state.task_plan.plan
    entry = collect_feedback(
        user_prompt=state.get("user_prompt", ""),
        plan_name=plan.name,
        techstack=plan.techstack,
        features=plan.features,
    )
    if entry:
        store.save(entry)
        rating, total = entry["rating"], store.total()
        stars = "★" * rating + "☆" * (5 - rating)
        print(f"\n✓ Feedback saved [{stars}] — {total} total entr{'y' if total == 1 else 'ies'}.")
    return {"coder_state": coder_state}


# ── Refinement graph agents ───────────────────────────────────────────────────

def patch_planner_agent(state: dict) -> dict:
    change_request: str = state["change_request"]
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        print("[Patch] No project to refine yet.")
        return {"status": "DONE"}
    plan: Plan = coder_state.task_plan.plan

    # Check enhance intent for the change request too
    enhance, palette, design_prompt = _design_for(change_request)
    if not design_prompt:
        design_prompt = state.get("design_prompt", "")

    files = list_files.run(".")

    resp = _llm_invoke_with_retry(
        llm_smart.with_structured_output(PatchPlan, method="json_mode"),
        patch_planner_prompt(change_request, plan.name, plan.techstack, files),
    )
    if resp is None or not resp.tasks:
        print("[Patch] Nothing to change.")
        return {"coder_state": coder_state, "status": "DONE"}

    print(f"\n[Patch] {resp.summary}")
    print(f"[Patch] Files to update: {[t.filepath for t in resp.tasks]}")

    patch_steps = [
        ImplementationTask(filepath=t.filepath, task_description=t.change_description)
        for t in resp.tasks
    ]
    patch_task_plan = TaskPlan(implementation_steps=patch_steps)
    patch_task_plan.plan = plan

    new_coder_state = CoderState(task_plan=patch_task_plan, current_step_idx=0)
    return {
        "coder_state": new_coder_state,
        "status": None,
        "design_prompt": design_prompt,
        "enhance": enhance,
        "palette": palette,
    }



def debugger_agent(state: dict) -> dict:
    """Uses the smart model to diagnose the exact logic bugs before the coder runs."""
    change_request: str = state.get("change_request", "")
    coder_state: CoderState = state.get("coder_state")
    if not change_request or coder_state is None:
        return {"coder_state": coder_state}

    plan: Plan = coder_state.task_plan.plan

    # Read all project files for full context
    file_contents: dict[str, str] = {}
    for f in plan.files:
        c = read_file.run(f.path)
        if c:
            file_contents[f.path] = c

    if not file_contents:
        return {"coder_state": coder_state}

    files_block = "\n\n".join(f"=== {fp} ===\n{c}" for fp, c in file_contents.items())
    target_files = [s.filepath for s in coder_state.task_plan.implementation_steps]

    prompt = f"""You are a DEBUGGER. The user reports: "{change_request}"

Project: {plan.name} ({plan.techstack})
Files to fix: {target_files}

ALL project files (read carefully before diagnosing):
{files_block}

Identify the EXACT lines causing the reported behavior in each target file.
For each file produce a precise fix description covering:
- what specific code is wrong and why (line numbers if helpful)
- exactly what the corrected logic should be

Return ONLY this JSON:
{{
  "diagnoses": [
    {{
      "filepath": "script.js",
      "root_cause": "line 17 catches '=' in the operator branch before the equals handler, so the equals branch is dead code",
      "fix_instructions": "full detailed instructions for the coder"
    }}
  ]
}}"""

    class _Diagnosis(BaseModel):
        diagnoses: list[dict] = Field(default_factory=list)

    try:
        resp = _llm_invoke_with_retry(
            llm_smart.with_structured_output(_Diagnosis, method="json_mode"),
            prompt,
        )
    except Exception:
        resp = None

    if resp and resp.diagnoses:
        diag_map = {d["filepath"]: d for d in resp.diagnoses if d.get("filepath")}
        for step in coder_state.task_plan.implementation_steps:
            if step.filepath in diag_map:
                d = diag_map[step.filepath]
                step.task_description = (
                    f"ROOT CAUSE: {d.get('root_cause', '')}\n\n"
                    f"FIX: {d.get('fix_instructions', step.task_description)}"
                )
                print(f"[Debugger] {step.filepath}: {d.get('root_cause', '')[:120]}")

    return {"coder_state": coder_state}


def patch_executor_agent(state: dict) -> dict:
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        print("[Executor] No coder_state — skipping relaunch.")
        return {}
    return {"coder_state": coder_state, **_run_executor(coder_state.task_plan.plan, coder_state)}


# ── Main graph ────────────────────────────────────────────────────────────────

graph = StateGraph(dict)
graph.add_node("planner",  planner_agent)
graph.add_node("architect", architect_agent)
graph.add_node("coder",    coder_agent)
graph.add_node("reviewer", reviewer_agent)
graph.add_node("executor", executor_agent)
graph.add_node("feedback", feedback_agent)

graph.add_edge("planner",  "architect")
graph.add_edge("architect", "coder")
graph.add_conditional_edges(
    "coder",
    lambda s: "reviewer" if s.get("status") == "DONE" else "coder",
    {"reviewer": "reviewer", "coder": "coder"},
)
graph.add_edge("reviewer", "executor")
graph.add_edge("executor", "feedback")
graph.add_edge("feedback", END)
graph.set_entry_point("planner")
agent = graph.compile()

# ── Refinement graph ──────────────────────────────────────────────────────────
# Uses the same coder_agent + reviewer_agent as the main graph so refinements
# get full cross-file context and inline fixes — not a lightweight patch path.

refinement_graph = StateGraph(dict)
refinement_graph.add_node("patch_planner",  patch_planner_agent)
refinement_graph.add_node("debugger",       debugger_agent)
refinement_graph.add_node("coder",          coder_agent)
refinement_graph.add_node("reviewer",       reviewer_agent)
refinement_graph.add_node("patch_executor", patch_executor_agent)

refinement_graph.add_conditional_edges(
    "patch_planner",
    lambda s: "patch_executor" if s.get("status") == "DONE" else "debugger",
    {"patch_executor": "patch_executor", "debugger": "debugger"},
)
refinement_graph.add_edge("debugger", "coder")
refinement_graph.add_conditional_edges(
    "coder",
    lambda s: "reviewer" if s.get("status") == "DONE" else "coder",
    {"reviewer": "reviewer", "coder": "coder"},
)
refinement_graph.add_edge("reviewer",       "patch_executor")
refinement_graph.add_edge("patch_executor", END)
refinement_graph.set_entry_point("patch_planner")
refinement_agent = refinement_graph.compile()
