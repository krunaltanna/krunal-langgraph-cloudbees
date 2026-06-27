"""
CI Diagnostics Agent — Entry Point

Runs the agent against one or all build scenarios and prints a structured
observability trace showing every decision the agent made and why.

Usage:
  python main.py                        # runs all 3 builds
  python main.py --build build-1042     # runs a specific build
  python main.py --build build-1057 --seed 42  # fixed random seed for reproducibility
"""

import argparse
import json
import logging
import os
import random
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from agent import app
from mock_data import BUILDS, QUESTIONS

# ---------------------------------------------------------------------------
# Logging — INFO level shows tool decisions; DEBUG shows raw LLM calls
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

SEPARATOR = "─" * 70


def print_header(title: str) -> None:
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")


def print_section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def print_trace(reasoning_trace: list[dict]) -> None:
    print_section("AGENT REASONING TRACE")
    for i, entry in enumerate(reasoning_trace, 1):
        print(f"\n  Step {i}: [{entry['node'].upper()}]")
        print(f"    Decision  : {entry['decision']}")
        print(f"    Rationale : {entry['rationale']}")
        if entry.get("tools_tried_so_far"):
            print(f"    Tried so far : {entry['tools_tried_so_far']}")
        if entry.get("failed_tools_so_far"):
            print(f"    Failed so far: {entry['failed_tools_so_far']}")


def print_tool_results(tool_results: dict, failed_tools: list) -> None:
    print_section("TOOL RESULTS SUMMARY")
    for tool_name, result in tool_results.items():
        status = "✗ FAILED (unusable)" if tool_name in failed_tools else "✓ SUCCESS"
        print(f"\n  [{status}] {tool_name}")
        # Truncate long results for readability
        result_str = str(result)
        if len(result_str) > 400:
            result_str = result_str[:400] + "... [truncated for display]"
        for line in result_str.splitlines():
            print(f"    {line}")


def print_diagnosis(state: dict) -> None:
    print_section("DIAGNOSIS")
    confidence_symbol = {"high": "●●●", "medium": "●●○", "low": "●○○"}.get(
        state.get("confidence", "low"), "●○○"
    )
    print(f"\n  Confidence        : {confidence_symbol} {state.get('confidence', 'unknown').upper()}")
    print(f"\n  Root Cause        : {state.get('diagnosis', 'No diagnosis produced.')}")
    print(f"\n  Recommended Action: {state.get('recommended_action', 'N/A')}")
    print(f"\n  Tools tried       : {state.get('tool_attempts', [])}")
    print(f"  Tools that failed : {state.get('failed_tools', [])}")


# ---------------------------------------------------------------------------
# Run one build scenario
# ---------------------------------------------------------------------------

def run_build(build_id: str) -> dict:
    build = BUILDS[build_id]
    question = QUESTIONS[build_id]

    print_header(f"CI DIAGNOSTICS AGENT  |  {build_id}  |  {datetime.now().strftime('%H:%M:%S')}")
    print(f"\n  Scenario : {build['description']}")
    print(f"  Question : {question}")
    print(f"  Repo     : {build['repo']}")
    print(f"  Flaky tool for this build: {build['flaky_tool']} "
          f"(failure probability: {int(build['flaky_probability'] * 100)}%)")

    initial_state: dict = {
        "question": question,
        "build_id": build_id,
        "repo": build["repo"],
        "messages": [],
        "tool_attempts": [],
        "failed_tools": [],
        "tool_results": {},
        "available_tools": [],
        "_next_tool": "",
        "_last_result_usable": True,
        "reasoning_trace": [],
        "diagnosis": "",
        "confidence": "",
        "recommended_action": "",
    }

    logger.info("Starting agent run for %s", build_id)
    final_state = app.invoke(initial_state)

    print_trace(final_state.get("reasoning_trace", []))
    print_tool_results(
        final_state.get("tool_results", {}),
        final_state.get("failed_tools", []),
    )
    print_diagnosis(final_state)

    return final_state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="CI Diagnostics Agent")
    parser.add_argument(
        "--build",
        choices=list(BUILDS.keys()),
        default=None,
        help="Run a specific build scenario (default: run all)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible failure injection (default: random)",
    )
    args = parser.parse_args()

    # Validate API key presence early
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    # Set random seed if provided (useful for generating sample_run.txt)
    if args.seed is not None:
        random.seed(args.seed)
        logger.info("Random seed set to %d — failure injection is deterministic", args.seed)

    builds_to_run = [args.build] if args.build else list(BUILDS.keys())

    results = {}
    for build_id in builds_to_run:
        try:
            state = run_build(build_id)
            results[build_id] = {
                "confidence": state.get("confidence"),
                "tools_tried": state.get("tool_attempts"),
                "tools_failed": state.get("failed_tools"),
                "diagnosis": state.get("diagnosis"),
            }
        except Exception as e:
            logger.error("Agent run failed for %s: %s", build_id, str(e))
            results[build_id] = {"error": str(e)}

    if len(builds_to_run) > 1:
        print_header("RUN SUMMARY")
        for build_id, result in results.items():
            if "error" in result:
                print(f"\n  {build_id}: ERROR — {result['error']}")
            else:
                fallback_occurred = bool(result.get("tools_failed"))
                fallback_label = " [FALLBACK TRIGGERED]" if fallback_occurred else ""
                print(
                    f"\n  {build_id}: confidence={result['confidence']}"
                    f"{fallback_label}"
                    f"\n    Tools: {result['tools_tried']}"
                    f"\n    Diagnosis: {result['diagnosis'][:100]}..."
                )

    print(f"\n{'═' * 70}\n")


if __name__ == "__main__":
    main()
