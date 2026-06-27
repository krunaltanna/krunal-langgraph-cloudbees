# CI Diagnostics Agent

A LangGraph-based agent that investigates why a CI/CD build failed. Given a build ID and a question, it decides which diagnostic tools to call, detects when a tool returns bad data, and falls back to alternative sources — producing a root-cause diagnosis with a confidence level.

## Why this scenario

A CI failure investigation requires real agentic behaviour: the agent must choose which tool is most relevant to the question, evaluate whether the data it gets back is actually useful, and recover when a tool fails. The decision tree isn't static — a question about test failures leads to a different starting tool than a question about a deployment failure, and a truncated log requires a different fallback than a timed-out test report. An if/else chain could not handle this cleanly; the branching depends on what the agent observes at runtime.

## Why LangGraph

LangGraph makes the decision structure explicit. Each node has a single responsibility, the conditional edge after `evaluate` is the only branching point, and the full reasoning trace is captured in state. This makes the agent's behaviour auditable — you can see exactly which tools were tried, in what order, and why. "I already know it" is also a valid reason; I've used it on production agentic pipelines and it was the fastest path to a clean, readable demo.

## Graph structure

```
[START] → plan → call_tool → evaluate → synthesize → [END]
                     ↑            |
                     |      (bad/insufficient)
                  fallback ←──────┘
```

Five nodes, one conditional edge:

| Node | What it does |
|---|---|
| `plan` | LLM picks which tool to call first based on the question |
| `call_tool` | Executes the chosen tool (reused by both plan and fallback) |
| `evaluate` | LLM checks if result is usable *and* sufficient to answer the question |
| `fallback` | LLM picks the next tool from those not yet tried |
| `synthesize` | LLM writes final diagnosis, confidence level, and recommended action |

## Three build scenarios

| Build | Story | Flaky tool |
|---|---|---|
| `build-1042` | `stripe-sdk` version bump broke payment gateway tests | `get_test_results` (timeout) |
| `build-1057` | Missing env var caused deployment failure despite passing tests | `get_build_logs` (truncated) |
| `build-1073` | DB migration conflict crashed service on startup | `get_test_results` (empty response) |

Each build has a different tool that fails and a different failure story, so the agent takes genuinely different paths across runs — not the same fallback logic with different data.

## How to run

```bash
# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY=your_key_here

# Run all three builds
python main.py

# Run a specific build
python main.py --build build-1042

# Run with a fixed seed (deterministic failure injection — useful for demos)
python main.py --build build-1057 --seed 1
```

Recommended demo seeds:
- `build-1042 --seed 1` → `get_test_results` times out, agent falls back to commits
- `build-1057 --seed 1` → `get_build_logs` truncated, agent falls back through test results to commits
- `build-1073 --seed 1` → `get_test_results` returns empty, agent falls back to commits

## Assumptions and shortcuts

- **Tools are mocked.** All three tool functions return fixture data from `mock_data.py`. The agent's reasoning, fallback logic, and synthesis are the focus — not real API integrations.
- **Single repo.** All builds belong to `payments-service`. A real system would parameterise this.
- **No persistence.** State lives only in memory for the duration of one run. A production agent would checkpoint state to survive restarts.
- **Observability is stdout.** The reasoning trace prints to console. In production this would go to LangSmith, Datadog, or a structured log sink.

## Failure mode: insufficient data stops too early

**The problem.** The first version of the `evaluate` node only checked whether tool output was *readable*. When `get_test_results` returned `47/47 tests passed`, the agent treated that as a complete answer and skipped `get_recent_commits` — even though all-green tests don't explain a deployment failure.

**The fix.** `evaluate` now checks two things: is the result usable (data intact), and is it *sufficient* (does it actually answer why the build failed). `_last_result_usable` is only `True` when both conditions are met. An all-green test result is usable but not sufficient when the build still failed — so the agent correctly continues to the next tool.

**Code snippet:**
```python
# evaluate node — usable AND sufficient, not just usable
return {
    **trace_patch,
    "failed_tools": failed_tools,
    "_last_result_usable": usable and sufficient,
}
```

Other failure modes handled:
- **Truncated / garbled output** → `evaluate` marks NOT USABLE, routes to fallback
- **Empty response `{}`** → same as above
- **All tools exhausted** → `should_fallback` forces `synthesize` with `confidence: low` rather than crashing
- **Agent loop** → `MAX_TOOL_ATTEMPTS = 3` hard ceiling in `should_fallback`
- **LLM returns JSON wrapped in markdown fences** → `_parse_json()` strips fences before parsing; all system prompts also explicitly say no code fences

## Observability

Every node appends a structured entry to `reasoning_trace` in state:
```python
{
    "node": "evaluate",
    "decision": "get_test_results result is USABLE but NOT SUFFICIENT",
    "rationale": "All 47 tests passed — confirms tests are not the cause, but does not explain the deployment failure",
    "tools_tried_so_far": ["get_build_logs", "get_test_results"],
    "failed_tools_so_far": ["get_build_logs"]
}
```

This prints as a step-by-step timeline at the end of each run. In production this would be shipped to a tracing backend so you can replay any run and see exactly what the agent observed and decided.

## Evaluation plan

Reliability would be assessed across three dimensions: **correctness** (does the final diagnosis match the known ground truth for each build scenario, measured across repeated runs with varying random seeds), **recovery rate** (what percentage of runs where the primary tool fails still produce a medium-or-higher confidence diagnosis), and **loop safety** (no run exceeds `MAX_TOOL_ATTEMPTS` regardless of LLM output). A systematic eval would run each build 20+ times, vary failure probabilities, and track confidence distribution — flagging any run that produces `confidence: low` when sufficient data was available.
