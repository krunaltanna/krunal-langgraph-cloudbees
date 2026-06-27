"""
Tool definitions for the CI Diagnostics Agent.

Each tool is a plain Python function that the LangGraph ToolNode will invoke.
Failure injection is controlled per-build via mock_data.BUILDS[build_id]["flaky_tool"]
and "flaky_probability" — no magic, fully transparent.

Tools:
  get_build_logs(build_id)      — fetches CI build log output
  get_test_results(build_id)    — fetches structured test report
  get_recent_commits(repo)      — fetches recent git commits (always reliable)

Failure modes demonstrated:
  - Truncated / garbled log stream  (get_build_logs)
  - Connection timeout string       (get_test_results)
  - Empty dict response             (get_test_results)
  - get_recent_commits never fails  — intentional: VCS is the last reliable source
"""

import json
import random
import logging
from langchain_core.tools import tool
from dotenv import load_dotenv

load_dotenv()

from mock_data import BUILDS

logger = logging.getLogger(__name__)


def _is_flaky(build_id: str, tool_name: str) -> bool:
    """Return True if this tool should simulate a failure for this build."""
    build = BUILDS.get(build_id, {})
    if build.get("flaky_tool") != tool_name:
        return False
    probability = build.get("flaky_probability", 0.5)
    return random.random() < probability


@tool
def get_build_logs(build_id: str) -> str:
    """
    Retrieve the CI build log for a given build ID.
    Returns a multi-line string of timestamped build steps.
    May return truncated output if the log stream was interrupted.
    """
    build = BUILDS.get(build_id)
    if not build:
        return f"ERROR: build_id '{build_id}' not found in system."

    if _is_flaky(build_id, "get_build_logs"):
        result = build["logs_truncated"]
        logger.warning("[tool:get_build_logs] Simulated failure for %s — truncated log", build_id)
        return result

    result = build["logs_healthy"]
    logger.info("[tool:get_build_logs] Success for %s", build_id)
    return result


@tool
def get_test_results(build_id: str) -> str:
    """
    Retrieve structured test results for a given build ID.
    Returns a JSON string with summary and per-failure details.
    May return a timeout message or empty JSON object if the test
    reporting service is unavailable.
    """
    build = BUILDS.get(build_id)
    if not build:
        return f"ERROR: build_id '{build_id}' not found in system."

    if _is_flaky(build_id, "get_test_results"):
        # Pick randomly among available failure variants for this build
        failure_variants = build["test_results_failures"]
        result = random.choice(failure_variants)
        logger.warning(
            "[tool:get_test_results] Simulated failure for %s — returned: %s",
            build_id,
            repr(result),
        )
        return json.dumps(result) if isinstance(result, dict) else result

    result = build["test_results_healthy"]
    logger.info("[tool:get_test_results] Success for %s", build_id)
    return json.dumps(result, indent=2)


@tool
def get_recent_commits(repo: str) -> str:
    """
    Retrieve the most recent git commits for a repository.
    Returns a JSON array of commit objects with sha, message, author,
    timestamp, and files_changed.
    This tool is always available — VCS history is reliable even when
    CI services are degraded.
    """
    # Find the build that matches this repo — use first match for simplicity
    # In a real system this would query the VCS API directly
    commits = None
    for build in BUILDS.values():
        if build["repo"] == repo:
            commits = build["commits"]
            break

    if commits is None:
        return f"ERROR: repository '{repo}' not found."

    logger.info("[tool:get_recent_commits] Success for repo %s", repo)
    return json.dumps(commits, indent=2)


# Exported list for LangGraph ToolNode and tool binding
TOOLS = [get_build_logs, get_test_results, get_recent_commits]

# Map tool name → function (used by ToolNode routing)
TOOL_MAP = {t.name: t for t in TOOLS}
