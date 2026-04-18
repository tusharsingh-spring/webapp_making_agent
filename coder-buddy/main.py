import argparse
import shutil
import sys
import traceback

from agent.graph import agent, refinement_agent, kill_running_app
from agent.tools import PROJECT_ROOT


_W = 60  # banner width


def _banner(title: str) -> None:
    print("\n" + "━" * _W)
    print(f"  {title}")
    print("━" * _W)


def _section(title: str) -> None:
    print(f"\n── {title} {'─' * (_W - len(title) - 4)}")


def main():
    parser = argparse.ArgumentParser(description="Coder Buddy — AI project generator")
    parser.add_argument("--recursion-limit", "-r", type=int, default=100)
    args = parser.parse_args()

    # Wipe any leftover files from a previous session
    if PROJECT_ROOT.exists():
        shutil.rmtree(PROJECT_ROOT)
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)

    _banner("CODER BUDDY  —  AI-powered project generator")
    print("  Describe the app you want and Coder Buddy will build it.")
    print("  After generation you can keep refining until satisfied.\n")

    try:
        user_prompt = input("  What do you want to build? ").strip()
        if not user_prompt:
            print("No prompt entered. Exiting.")
            sys.exit(0)

        # ── Initial generation ────────────────────────────────────────────────
        _section("Generating your project")
        state = agent.invoke(
            {"user_prompt": user_prompt},
            {"recursion_limit": args.recursion_limit},
        )

        # ── Iterative refinement loop (app runs in background) ────────────────
        while True:
            _banner("YOUR APP IS RUNNING IN THE BROWSER")
            print("  Describe any change you want — UI, logic, features, redesign, anything.")
            print("  Press Enter with no input to quit.\n")

            try:
                change = input("  What do you want to change? ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nDone.")
                break

            if not change:
                print("\nGoodbye! Your project is in the generated_project/ folder.")
                break

            _section(f"Applying: {change}")

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
