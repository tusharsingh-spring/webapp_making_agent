import argparse
import shutil
import sys
import traceback

from agent.graph import agent, refinement_agent, kill_running_app
from agent.tools import PROJECT_ROOT


def main():
    parser = argparse.ArgumentParser(description="Coder Buddy — AI project generator")
    parser.add_argument("--recursion-limit", "-r", type=int, default=100)
    args = parser.parse_args()

    # Wipe any leftover files from a previous session
    if PROJECT_ROOT.exists():
        shutil.rmtree(PROJECT_ROOT)
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)

    try:
        user_prompt = input("Enter your project prompt: ").strip()
        if not user_prompt:
            print("No prompt entered. Exiting.")
            sys.exit(0)

        # ── Initial generation ────────────────────────────────────────────────
        state = agent.invoke(
            {"user_prompt": user_prompt},
            {"recursion_limit": args.recursion_limit},
        )

        # ── Iterative refinement loop (app runs in background) ────────────────
        while True:
            print("\n" + "=" * 50)
            print("App is running in the browser. What would you like to change?")
            print("  e.g. 'fix the X and O clicks', 'make it dark themed', 'add animations'")
            print("  Press Enter with no input to quit.")
            print("=" * 50)

            try:
                change = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nDone.")
                break

            if not change:
                print("\nGoodbye!")
                break

            print(f"\n[Refinement] Applying: {change!r}")

            try:
                state = refinement_agent.invoke(
                    {
                        "change_request": change,
                        "coder_state":   state.get("coder_state"),
                        "user_prompt":   user_prompt,
                        "lessons":       state.get("lessons", ""),
                        "design_prompt": state.get("design_prompt", ""),
                        "enhance":       state.get("enhance", False),
                        "palette":       state.get("palette", ""),
                    },
                    {"recursion_limit": args.recursion_limit},
                )
                # Keep coder_state in sync after refinement
                if state.get("coder_state") is None:
                    print("[WARN] coder_state lost after refinement — future patches may fail.")
            except KeyboardInterrupt:
                print("\nRefinement cancelled.")
            except Exception:
                traceback.print_exc()
                print("\n[ERROR] Refinement failed — project files unchanged.")

    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        kill_running_app()


if __name__ == "__main__":
    main()
