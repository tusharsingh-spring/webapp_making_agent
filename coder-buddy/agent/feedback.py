import json
from datetime import datetime
from pathlib import Path

FEEDBACK_FILE = Path(__file__).parent.parent / "feedback" / "feedback_store.json"


class FeedbackStore:
    def __init__(self):
        FEEDBACK_FILE.parent.mkdir(exist_ok=True)
        if not FEEDBACK_FILE.exists():
            FEEDBACK_FILE.write_text("[]")

    def save(self, entry: dict) -> None:
        entries = self._load()
        entries.append(entry)
        FEEDBACK_FILE.write_text(json.dumps(entries, indent=2))

        # Auto-trigger tuner every N entries
        from agent.tuner import should_tune, run_tuner
        if should_tune(len(entries)):
            print("\n[Tuner] Analysing feedback to update agent rules…")
            rules = run_tuner(entries)
            p = len(rules.planner_rules)
            a = len(rules.architect_rules)
            c = len(rules.coder_rules)
            print(f"[Tuner] Rules updated — Planner:{p}  Architect:{a}  Coder:{c}")
            print(f"[Tuner] Saved to feedback/agent_rules.json")

    def _load(self) -> list:
        try:
            return json.loads(FEEDBACK_FILE.read_text())
        except Exception:
            return []

    def get_lessons(self, techstack: str, limit: int = 3) -> str:
        """Raw feedback snippets — injected as context, not rules."""
        all_entries = self._load()
        if not all_entries:
            return ""

        tags = {t.strip().lower() for t in techstack.split(",")} if techstack else set()

        def relevance(e):
            entry_tags = {t.strip().lower() for t in e.get("techstack", "").split(",")}
            return len(tags & entry_tags) if tags else 0

        picked = sorted(all_entries, key=relevance, reverse=True)[:limit]

        lines = []
        for e in picked:
            parts = [f"• [{e.get('techstack','?')} | {e.get('rating','?')}/5]"]
            for key, label in [
                ("what_worked",        "Worked"),
                ("what_failed",        "Failed"),
                ("planner_feedback",   "Planner"),
                ("architect_feedback", "Architect"),
                ("coder_feedback",     "Coder"),
            ]:
                val = e.get(key, "").strip()
                if val:
                    parts.append(f"  {label}: {val}")
            lines.append("\n".join(parts))

        return "\n\n".join(lines)

    def total(self) -> int:
        return len(self._load())


def collect_feedback(user_prompt: str, plan_name: str,
                     techstack: str, features: list) -> dict | None:
    print(f"\n{'='*50}")
    print("FEEDBACK — help improve future generations")
    print(f"{'='*50}")
    print(f"Project : {plan_name}")
    print(f"Stack   : {techstack}")
    print()

    try:
        raw = input("Rate this generation (1=poor … 5=excellent, Enter to skip): ").strip()
        if not raw:
            print("Feedback skipped.")
            return None
        rating = max(1, min(5, int(raw)))

        what_worked = input("What worked well?          (Enter to skip): ").strip()
        what_failed = input("What could be improved?    (Enter to skip): ").strip()

    except (ValueError, EOFError, KeyboardInterrupt):
        print("\nFeedback skipped.")
        return None

    return {
        "timestamp":   datetime.now().isoformat(),
        "user_prompt": user_prompt,
        "plan_name":   plan_name,
        "techstack":   techstack,
        "features":    features,
        "rating":      rating,
        "what_worked": what_worked,
        "what_failed": what_failed,
    }
