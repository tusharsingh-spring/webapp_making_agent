"""
Feedback Tuner — analyzes accumulated user feedback with an LLM and
synthesizes concrete, per-agent rules saved to feedback/agent_rules.json.

Runs automatically every TRIGGER_EVERY new feedback entries so agents
improve without manual intervention.
"""

import json
from datetime import datetime
from pathlib import Path

from langchain_groq.chat_models import ChatGroq
from pydantic import BaseModel, Field

RULES_FILE = Path(__file__).parent.parent / "feedback" / "agent_rules.json"
TRIGGER_EVERY = 3          # re-tune after every N new feedback entries
MAX_RULES_PER_AGENT = 10   # cap so prompts don't grow unbounded

_llm = None

def _get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        _llm = ChatGroq(model="llama-3.1-8b-instant")
    return _llm


# ── Data model ────────────────────────────────────────────────────────────────

class AgentRules(BaseModel):
    planner_rules:   list[str] = Field(default_factory=list)
    architect_rules: list[str] = Field(default_factory=list)
    coder_rules:     list[str] = Field(default_factory=list)
    tuned_at:        str       = Field(default="")
    based_on:        int       = Field(default=0, description="Number of feedback entries used")


class _RulesResponse(BaseModel):
    planner_rules:   list[str] = Field(description="Rules for the Planner agent")
    architect_rules: list[str] = Field(description="Rules for the Architect agent")
    coder_rules:     list[str] = Field(description="Rules for the Coder agent")


# ── Persistence ───────────────────────────────────────────────────────────────

def load_rules() -> AgentRules:
    if RULES_FILE.exists():
        try:
            return AgentRules(**json.loads(RULES_FILE.read_text()))
        except Exception:
            pass
    return AgentRules()


def _save_rules(rules: AgentRules) -> None:
    RULES_FILE.parent.mkdir(exist_ok=True)
    RULES_FILE.write_text(rules.model_dump_json(indent=2))


# ── Core tuning logic ─────────────────────────────────────────────────────────

def should_tune(total_feedback: int) -> bool:
    """True when we have at least TRIGGER_EVERY new entries since last tune."""
    return total_feedback >= load_rules().based_on + TRIGGER_EVERY


def run_tuner(feedback_entries: list[dict]) -> AgentRules:
    """
    Use the LLM to analyse all feedback entries and synthesise
    per-agent improvement rules.  Saves and returns the new AgentRules.
    """
    if not feedback_entries:
        return load_rules()

    # ── Format feedback for the LLM ──────────────────────────────────────────
    blocks = []
    for i, e in enumerate(feedback_entries, 1):
        lines = [
            f"[{i}] {e.get('techstack','?')} | rating {e.get('rating','?')}/5",
            f"  Prompt   : {e.get('user_prompt','')}",
        ]
        for key, label in [
            ("what_worked",        "Worked"),
            ("what_failed",        "Failed"),
            ("planner_feedback",   "Planner"),
            ("architect_feedback", "Architect"),
            ("coder_feedback",     "Coder"),
        ]:
            val = e.get(key, "").strip()
            if val:
                lines.append(f"  {label:<10}: {val}")
        blocks.append("\n".join(lines))

    feedback_text = "\n\n".join(blocks)

    prompt = f"""
You are analysing user feedback on an AI code-generation system to improve it.

The system has three agents:
  • PLANNER   — decides tech stack, file structure, features
  • ARCHITECT — breaks the plan into per-file implementation tasks
  • CODER     — writes the actual code for each file

Feedback from {len(feedback_entries)} past generations:

{feedback_text}

Your task: extract SPECIFIC, ACTIONABLE rules for each agent based on
recurring patterns in the feedback.  Rules must:
  - Be concrete ("Always X", "Never Y", "When Z do W")
  - Directly fix an observed failure
  - Apply to future generations, not just the past ones
  - Maximum {MAX_RULES_PER_AGENT} rules per agent

Return ONLY a JSON object:
{{
  "planner_rules":   ["rule", ...],
  "architect_rules": ["rule", ...],
  "coder_rules":     ["rule", ...]
}}
"""

    try:
        resp = _get_llm().with_structured_output(_RulesResponse, method="json_mode").invoke(prompt)
    except Exception as e:
        print(f"[Tuner] LLM call failed ({e}) — keeping existing rules.")
        return load_rules()

    rules = AgentRules(
        planner_rules=resp.planner_rules[:MAX_RULES_PER_AGENT] if resp else [],
        architect_rules=resp.architect_rules[:MAX_RULES_PER_AGENT] if resp else [],
        coder_rules=resp.coder_rules[:MAX_RULES_PER_AGENT] if resp else [],
        tuned_at=datetime.now().isoformat(),
        based_on=len(feedback_entries),
    )
    _save_rules(rules)
    return rules


# ── Prompt formatting ─────────────────────────────────────────────────────────

def format_rules_block(rules: list[str], agent_name: str) -> str:
    """Return a rules block ready for injection into a prompt."""
    if not rules:
        return ""
    numbered = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))
    return f"""
LEARNED RULES for {agent_name} (derived from user feedback — follow strictly):
{numbered}
"""
