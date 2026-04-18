def _lessons_block(lessons: str) -> str:
    if not lessons:
        return ""
    return f"""
CONTEXT FROM PAST GENERATIONS:
{lessons}
"""


def planner_prompt(user_prompt: str, lessons: str = "", rules: str = "") -> str:
    return f"""
You are the PLANNER agent. Convert the user's request into a precise project plan.
{rules}
{_lessons_block(lessons)}
User request (build EXACTLY this — not a generic version):
  "{user_prompt}"

Base rules:
- Plan name, features, and files must directly reflect the request above.
- Web apps (HTML/CSS/JS): NO React/Vue/build-step frameworks. Vanilla JS + CDN only.
- Python apps: standard library only unless the request explicitly needs a package.
- Keep the file list minimal — only files truly needed to fulfil the request.

Return ONLY this JSON:
{{
  "name": "exact app name from request",
  "description": "one-line description matching the request",
  "techstack": "comma-separated tech stack",
  "features": ["feature matching request", ...],
  "files": [{{"path": "filename", "purpose": "what this specific file does"}}]
}}
"""


def architect_prompt(plan: str, user_prompt: str = "", lessons: str = "", rules: str = "") -> str:
    user_ctx = f'\nOriginal request: "{user_prompt}"\n' if user_prompt else ""
    return f"""
You are the ARCHITECT agent. Break this project plan into implementation tasks.
{rules}
{user_ctx}{_lessons_block(lessons)}
Base rules:
- One task per file.
- Each task must specify: functions/classes/variables to create, their signatures,
  how they integrate with other files, and what user-visible behaviour they produce.
- Order tasks so dependencies come first.

Project Plan:
{plan}

Return ONLY this JSON:
{{
  "implementation_steps": [
    {{"filepath": "path/to/file", "task_description": "detailed task"}}
  ]
}}
"""


def coder_system_prompt(plan=None, user_prompt: str = "",
                        lessons: str = "", design_prompt: str = "",
                        rules: str = "") -> str:
    is_web = plan and any(
        t in plan.techstack.lower()
        for t in ["html", "css", "js", "javascript", "web"]
    )
    user_ctx = (
        f'\nThe user asked for: "{user_prompt}"\n'
        f'Every file must directly serve this request.\n'
    ) if user_prompt else ""

    if design_prompt and is_web:
        web_section = design_prompt
    elif is_web:
        web_section = """
CRITICAL FOR WEB APPS:
- index.html must be COMPLETE and self-contained.
- Load ALL libraries via CDN <script> tags — no ES module imports or JSX.
- CSS in <style> tags or a linked .css file.
- Must work when opened directly in a browser (no server required).
- Include real styling, colours, and layout — not a skeleton.
"""
    else:
        web_section = ""

    return f"""
You are the CODER agent implementing a specific file for a real project.
{rules}
{user_ctx}{web_section}
{_lessons_block(lessons)}
Base rules:
- Write the COMPLETE, final file — no placeholders, no TODOs, no stub functions.
- Every function/class/style mentioned in the task description must be implemented.
- Use consistent names across all files.
- Python entry points: include if __name__ == '__main__':
- HTML: include DOCTYPE, charset, viewport meta, all CSS/JS linked or inline.
"""


def reviewer_prompt(plan_name: str, techstack: str, files_contents: str) -> str:
    return f"""
You are the REVIEWER agent. Inspect the generated project files for quality issues.

Project : {plan_name}
Stack   : {techstack}

Generated files:
{files_contents}

Check for these issues:
- Empty or near-empty files (< 5 meaningful lines)
- CSS files with no colours, no layout, or only comments
- HTML files missing <script> or <link> tags for their dependencies
- JS files with unimplemented stub functions (e.g. "// TODO")
- Python files missing the main entry-point block
- Files that don't match the project description at all

Return ONLY a JSON object:
{{
  "has_issues": true or false,
  "issues": [
    {{"filepath": "style.css", "problem": "exact problem", "fix": "what must be done"}}
  ],
  "summary": "one-line overall verdict"
}}
"""


def patch_planner_prompt(change_request: str, plan_name: str,
                         techstack: str, files_summary: str) -> str:
    return f"""
You are the PATCH PLANNER. A user wants to modify an already-generated project.

Project : {plan_name} ({techstack})
Files   : {files_summary}

Change request (do EXACTLY this, nothing more):
  "{change_request}"

Identify the minimum set of files that must change and describe precisely what to do.

Return ONLY a JSON object:
{{
  "tasks": [
    {{"filepath": "file.css", "change_description": "exact change to make"}}
  ],
  "summary": "one-line summary of all changes"
}}
"""


def executor_prompt() -> str:
    return """
You are the EXECUTOR agent. Produce the exact shell commands to set up and run this project.

CRITICAL: base your answer ONLY on the files listed — do NOT invent files that are not there.

Rules:
- setup_commands: only if package.json or requirements.txt is listed. Otherwise [].
- run_command — choose based on what files actually exist:
    * Only .html/.css/.js files present → "python -m http.server 8080"
    * Python entry point (main.py/app.py/calculator.py etc.) → "python <that file>"
    * server.js is listed → "node server.js"
    * NEVER use "node server.js" when server.js is not in the file list.
- open_url: "http://localhost:8080" for http.server, "http://localhost:3000" for node, "" for CLI.
- notes: one sentence on what the user will see.

Return JSON: {"setup_commands": [...], "run_command": "...", "open_url": "...", "notes": "..."}
"""
