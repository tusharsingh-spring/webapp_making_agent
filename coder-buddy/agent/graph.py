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


def _llm_invoke_with_retry(chain, prompt: str, retries: int = 3):
    """Invoke an LLM chain, retrying on rate-limit errors with the exact wait from the error."""
    for attempt in range(retries):
        try:
            return chain.invoke(prompt)
        except Exception as e:
            msg = str(e)
            if "rate_limit_exceeded" in msg or "429" in msg:
                wait = _parse_retry_after(msg)
                print(f"\n[Rate limit] Waiting {wait:.0f}s (retry {attempt + 1}/{retries})…")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("LLM rate limit: all retries exhausted.")

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

llm = ChatGroq(model="llama-3.1-8b-instant")
store = FeedbackStore()


def _load_agent_rules() -> tuple[str, str, str]:
    """Returns (planner_rules_str, architect_rules_str, coder_rules_str)."""
    rules = load_rules()
    return (
        format_rules_block(rules.planner_rules,   "PLANNER"),
        format_rules_block(rules.architect_rules, "ARCHITECT"),
        format_rules_block(rules.coder_rules,     "CODER"),
    )


class FileContent(BaseModel):
    content: str = Field(description="The complete content of the file to be written")


def _extract_from_failed(failed: str) -> "FileContent | None":
    """Extract file content from Groq's failed_generation string."""

    def _unescape(s: str) -> str:
        return s.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")

    # 1. Replace triple-quoted values with proper JSON strings, then parse
    def _fix_triple(s: str) -> str:
        return re.sub(
            r'"""([\s\S]*?)"""',
            lambda m: '"' + m.group(1).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"',
            s,
        )

    for candidate in (failed, _fix_triple(failed)):
        try:
            data = _json.loads(candidate)
            content = data.get("content", "")
            if content.strip():
                print("[Coder] Recovered content via JSON parse.")
                return FileContent(content=content)
        except Exception:
            pass

    # 2. Triple-quote bare extraction (lazy, handles literal \n at end)
    m = re.search(r'"content"\s*:\s*"""([\s\S]+?)"""', failed)
    if m:
        print("[Coder] Recovered content from triple-quoted generation.")
        return FileContent(content=_unescape(m.group(1)))

    # 3. Greedy single-quoted extraction
    m = re.search(r'"content"\s*:\s*"([\s\S]+)"', failed)
    if m:
        raw = _unescape(m.group(1))
        if raw.strip():
            print("[Coder] Recovered content from failed JSON generation.")
            return FileContent(content=raw)

    return None


def _invoke_file_content(prompt: str) -> "FileContent | None":
    """json_mode LLM call with fallback extraction when Groq rejects the output."""
    try:
        return _llm_invoke_with_retry(llm.with_structured_output(FileContent, method="json_mode"), prompt)
    except Exception as e:
        failed = ""
        if hasattr(e, "response"):
            try:
                failed = e.response.json().get("error", {}).get("failed_generation", "")
            except Exception:
                pass
        if not failed:
            raise
        result = _extract_from_failed(failed)
        if result:
            return result
        raise


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


def _run_executor(plan: Plan, coder_state: CoderState) -> dict:
    """Launch the app non-blocking: stream boot logs, open browser, return immediately."""
    global _running_proc

    # Kill any previously running app before relaunching
    kill_running_app()

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

    resp = _llm_invoke_with_retry(llm.with_structured_output(Plan, method="json_mode"),
        planner_prompt(user_prompt, lessons=lessons, rules=p_rules)
    )
    if resp is None:
        raise ValueError("Planner returned nothing.")
    return {"plan": resp, "lessons": lessons, "enhance": enhance,
            "design_prompt": design_prompt, "palette": palette,
            "arch_rules": a_rules, "coder_rules": c_rules}


def architect_agent(state: dict) -> dict:
    plan: Plan = state["plan"]
    user_prompt: str = state.get("user_prompt", "")
    a_rules: str = state.get("arch_rules", "")
    lessons: str = store.get_lessons(techstack=plan.techstack, limit=3)

    resp = _llm_invoke_with_retry(
        llm.with_structured_output(TaskPlan, method="json_mode"),
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

    prompt = (
        f"{coder_system_prompt(plan, user_prompt=user_prompt, lessons=lessons, design_prompt=design_prompt, rules=c_rules)}\n\n"
        f"Task: {current_task.task_description}\n"
        f"File: {current_task.filepath}\n"
        f"Existing content:\n{existing_content}\n\n"
        'Return ONLY valid JSON: {"content": "...file content..."}\n'
        'Rules: escape newlines as \\n, escape double quotes as \\", do NOT use triple quotes.'
    )

    resp = _invoke_file_content(prompt)
    if resp and resp.content.strip():
        write_file.run({"path": current_task.filepath, "content": resp.content})
        print(f"Wrote: {current_task.filepath}")

    coder_state.current_step_idx += 1
    return {"coder_state": coder_state}


def reviewer_agent(state: dict) -> dict:
    """
    Reviews all generated files for quality issues.
    If problems found, creates fix tasks and resets coder_state to patch them.
    Runs at most once (review_done flag prevents infinite loops).
    """
    if state.get("review_done"):
        return {"status": "DONE", "coder_state": state.get("coder_state")}

    coder_state: CoderState = state["coder_state"]
    plan: Plan = coder_state.task_plan.plan
    c_rules: str = state.get("coder_rules", "")

    # Read all generated files
    file_entries = []
    for task in coder_state.task_plan.implementation_steps:
        content = read_file.run(task.filepath)
        if content:
            preview = content[:300].replace("\n", " ")
            file_entries.append(f"--- {task.filepath} ({len(content)} chars) ---\n{preview}…")

    if not file_entries:
        return {"status": "DONE", "review_done": True, "coder_state": coder_state}

    files_block = "\n\n".join(file_entries)

    class ReviewResult(BaseModel):
        has_issues: bool = Field(description="True if any quality issues were found")
        issues: list[dict] = Field(default_factory=list, description="List of {filepath, problem, fix}")
        summary: str = Field(description="One-line verdict")

    resp = _llm_invoke_with_retry(
        llm.with_structured_output(ReviewResult, method="json_mode"),
        reviewer_prompt(plan.name, plan.techstack, files_block)
    )

    if resp is None or not resp.has_issues:
        print(f"[Reviewer] ✓ {resp.summary if resp else 'Files look good.'}")
        return {"status": "DONE", "review_done": True, "coder_state": coder_state}

    print(f"[Reviewer] Issues found: {resp.summary}")
    fix_steps = [
        ImplementationTask(
            filepath=issue.get("filepath", ""),
            task_description=issue.get("fix", "Fix quality issues in this file"),
        )
        for issue in resp.issues
        if issue.get("filepath")
    ]

    if not fix_steps:
        return {"status": "DONE", "review_done": True, "coder_state": coder_state}

    for issue in resp.issues:
        print(f"  • {issue.get('filepath')}: {issue.get('problem')}")

    fix_plan = TaskPlan(implementation_steps=fix_steps)
    fix_plan.plan = plan
    new_coder_state = CoderState(task_plan=fix_plan, current_step_idx=0)
    return {"coder_state": new_coder_state, "status": None, "review_done": True,
            "coder_rules": c_rules}


def executor_agent(state: dict) -> dict:
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        print("[Executor] No project state — skipping launch.")
        return {}
    result = _run_executor(coder_state.task_plan.plan, coder_state)
    return result


def feedback_agent(state: dict) -> dict:
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        return {}  # generation was interrupted before coder finished
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
    return {}


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
        llm.with_structured_output(PatchPlan, method="json_mode"),
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


def patch_coder_agent(state: dict) -> dict:
    """Coder in patch mode — reads existing file, applies change, writes back."""
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        return {"status": "DONE"}
    design_prompt: str = state.get("design_prompt", "")
    user_prompt: str = state.get("user_prompt", "")
    change_request: str = state.get("change_request", "")

    steps = coder_state.task_plan.implementation_steps
    if coder_state.current_step_idx >= len(steps):
        return {"coder_state": coder_state, "status": "DONE"}

    current_task = steps[coder_state.current_step_idx]
    existing_content = read_file.run(current_task.filepath)
    plan: Plan = coder_state.task_plan.plan

    prompt = (
        f"{coder_system_prompt(plan, user_prompt=user_prompt, design_prompt=design_prompt)}\n\n"
        f"You are PATCHING an existing file based on a user change request.\n"
        f"Change request: {change_request}\n"
        f"Specific change for this file: {current_task.task_description}\n\n"
        f"File: {current_task.filepath}\n"
        f"CURRENT content (keep everything that isn't being changed):\n{existing_content}\n\n"
        'Return ONLY valid JSON: {"content": "...full updated file..."}\n'
        'Rules: escape newlines as \\n, escape double quotes as \\", do NOT use triple quotes.'
    )

    resp = _invoke_file_content(prompt)
    if resp and resp.content.strip():
        write_file.run({"path": current_task.filepath, "content": resp.content})
        print(f"Patched: {current_task.filepath}")

    coder_state.current_step_idx += 1
    return {"coder_state": coder_state}


def patch_executor_agent(state: dict) -> dict:
    coder_state: CoderState = state["coder_state"]
    return _run_executor(coder_state.task_plan.plan, coder_state)


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
graph.add_conditional_edges(
    "reviewer",
    lambda s: "feedback" if s.get("status") == "DONE" else "coder",
    {"feedback": "feedback", "coder": "coder"},
)
graph.add_edge("feedback", "executor")
graph.add_edge("executor", END)
graph.set_entry_point("planner")
agent = graph.compile()

# ── Refinement graph ──────────────────────────────────────────────────────────

refinement_graph = StateGraph(dict)
refinement_graph.add_node("patch_planner", patch_planner_agent)
refinement_graph.add_node("patch_coder",   patch_coder_agent)
refinement_graph.add_node("patch_executor", patch_executor_agent)

refinement_graph.add_conditional_edges(
    "patch_planner",
    lambda s: "patch_executor" if s.get("status") == "DONE" else "patch_coder",
    {"patch_executor": "patch_executor", "patch_coder": "patch_coder"},
)
refinement_graph.add_conditional_edges(
    "patch_coder",
    lambda s: "patch_executor" if s.get("status") == "DONE" else "patch_coder",
    {"patch_executor": "patch_executor", "patch_coder": "patch_coder"},
)
refinement_graph.add_edge("patch_executor", END)
refinement_graph.set_entry_point("patch_planner")
refinement_agent = refinement_graph.compile()
