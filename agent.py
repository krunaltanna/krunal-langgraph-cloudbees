"""
CI Diagnostics Agent — LangGraph implementation.

Graph structure:
  [START] → plan → call_tool → evaluate → synthesize → [END]
                       ↑           |
                       |     (bad result)
                    fallback ←─────┘

Node responsibilities:
  plan       : LLM decides which tool to call first given the question
  call_tool  : executes the chosen tool via LangGraph ToolNode
  evaluate   : LLM inspects tool output and decides if it is usable
  fallback   : LLM picks the next tool to try from remaining options
  synthesize : LLM produces final diagnosis, confidence level, and recommended action

State:
  All decisions, tool results, and reasoning steps are recorded in AgentState
  and printed as an observability trace at the end of each run.
"""

import json
import logging
from typing import Annotated, Any
from typing_extensions import TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from tools import TOOLS, TOOL_MAP
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _parse_json(text: str) -> dict:
    """
    Robustly parse a JSON response from the LLM.
    Handles cases where the model wraps output in markdown code fences.
    """
    text = text.strip()
    # Strip markdown code fences if present: ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (```json or ```) and last line (```)
        text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
llm_with_tools = llm.bind_tools(TOOLS)

MAX_TOOL_ATTEMPTS = 3  # hard ceiling to prevent loops

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    # Core task inputs
    question: str
    build_id: str
    repo: str

    # LangGraph message history (append-only via add_messages reducer)
    messages: Annotated[list, add_messages]

    # Tracking
    tool_attempts: list[str]          # tools tried in order
    failed_tools: list[str]           # tools that returned unusable data
    tool_results: dict[str, Any]      # raw results keyed by tool name
    available_tools: list[str]        # tools not yet tried

    # Inter-node communication — must be in schema or LangGraph drops them
    _next_tool: str                   # set by plan/fallback, read by call_tool
    _last_result_usable: bool         # set by evaluate, read by should_fallback

    # Reasoning trace — every decision node appends one entry here
    reasoning_trace: list[dict]

    # Output
    diagnosis: str
    confidence: str                   # "high" | "medium" | "low"
    recommended_action: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _append_trace(state: AgentState, node: str, decision: str, rationale: str) -> dict:
    """Return a state patch that appends one trace entry."""
    entry = {
        "node": node,
        "decision": decision,
        "rationale": rationale,
        "tools_tried_so_far": list(state.get("tool_attempts", [])),
        "failed_tools_so_far": list(state.get("failed_tools", [])),
    }
    return {"reasoning_trace": state.get("reasoning_trace", []) + [entry]}


def _tool_names() -> list[str]:
    return [t.name for t in TOOLS]


# ---------------------------------------------------------------------------
# Node: plan
# LLM decides which tool to call first based on the question.
# ---------------------------------------------------------------------------

PLAN_SYSTEM = """You are a CI/CD diagnostic expert. Your job is to investigate why a build failed.

You have access to three tools:
- get_build_logs: returns timestamped build step output. Best for understanding WHAT step failed.
- get_test_results: returns structured JSON with pass/fail counts and error messages. Best for understanding WHY tests failed.
- get_recent_commits: returns recent git commits. Best for understanding WHAT CHANGED before the failure.

Given the user's question, decide which single tool to call FIRST.
Respond with ONLY a raw JSON object — no markdown, no code fences, no explanation:
{
  "first_tool": "<tool_name>",
  "rationale": "<one sentence explaining why this tool first>"
}"""


def plan(state: AgentState) -> dict:
    logger.info("[node:plan] Deciding first tool for build %s", state["build_id"])

    response = llm.invoke([
        SystemMessage(content=PLAN_SYSTEM),
        HumanMessage(content=(
            f"Question: {state['question']}\n"
            f"Build ID: {state['build_id']}\n"
            f"Repo: {state['repo']}"
        )),
    ])

    try:
        parsed = _parse_json(response.content)
        first_tool = parsed["first_tool"]
        rationale = parsed["rationale"]
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # Safe default if LLM produces unexpected output
        first_tool = "get_build_logs"
        rationale = "Defaulting to build logs — could not parse plan response."
        logger.warning("[node:plan] Failed to parse LLM response (%s), defaulting to get_build_logs", e)

    # Ensure chosen tool is valid
    if first_tool not in _tool_names():
        first_tool = "get_build_logs"
        rationale = f"Invalid tool suggested, defaulting to get_build_logs."

    all_tools = _tool_names()
    available = [t for t in all_tools if t != first_tool]

    trace_patch = _append_trace(state, "plan", f"Call {first_tool} first", rationale)

    return {
        **trace_patch,
        "available_tools": available,
        "tool_attempts": [],
        "failed_tools": [],
        "tool_results": {},
        "messages": [
            HumanMessage(content=(
                f"Investigate this CI failure.\n"
                f"Question: {state['question']}\n"
                f"Build ID: {state['build_id']}\n"
                f"Repo: {state['repo']}\n"
                f"Start by calling: {first_tool}"
            ))
        ],
        # Store chosen tool so call_tool knows what to invoke
        "_next_tool": first_tool,
    }


# ---------------------------------------------------------------------------
# Node: call_tool
# Executes the tool chosen by plan or fallback.
# ---------------------------------------------------------------------------

def call_tool(state: AgentState) -> dict:
    next_tool = state.get("_next_tool")
    build_id = state["build_id"]
    repo = state["repo"]

    logger.info("[node:call_tool] Invoking tool: %s", next_tool)

    tool_fn = TOOL_MAP.get(next_tool)
    if tool_fn is None:
        result = f"ERROR: tool '{next_tool}' not found."
        logger.error("[node:call_tool] Unknown tool: %s", next_tool)
    else:
        # Call the tool with the right argument
        if next_tool == "get_recent_commits":
            result = tool_fn.invoke({"repo": repo})
        else:
            result = tool_fn.invoke({"build_id": build_id})

    tool_results = dict(state.get("tool_results", {}))
    tool_results[next_tool] = result

    tool_attempts = list(state.get("tool_attempts", [])) + [next_tool]

    # Add as ToolMessage so LLM has full context in evaluate
    tool_msg = ToolMessage(
        content=str(result),
        tool_call_id=f"call_{next_tool}_{len(tool_attempts)}",
        name=next_tool,
    )

    trace_patch = _append_trace(
        state,
        "call_tool",
        f"Executed {next_tool}",
        f"Raw result length: {len(str(result))} chars",
    )

    return {
        **trace_patch,
        "tool_results": tool_results,
        "tool_attempts": tool_attempts,
        "messages": [tool_msg],
    }


# ---------------------------------------------------------------------------
# Node: evaluate
# LLM inspects the tool result and decides if it is usable.
# ---------------------------------------------------------------------------

EVALUATE_SYSTEM = """You are evaluating a tool result to decide two things:
1. Is it USABLE? (is the data intact and readable)
2. Is it SUFFICIENT? (does it actually answer why the build failed, or do we need more data)

A result is NOT usable if it is:
- An empty object: {}
- A timeout or connection error message
- Truncated or garbled (contains "ERROR:" or "Partial data only")

A result IS usable but NOT sufficient if:
- Tests all passed but the build still failed — we know tests aren't the cause, but we don't know the actual cause yet
- The data confirms one thing is fine but doesn't explain the root failure
- We have partial information that narrows the problem but doesn't identify it

A result IS usable AND sufficient if:
- It directly shows the error, exception, or failure reason
- It points to a specific file, config, or change that caused the failure

Respond with ONLY a raw JSON object — no markdown, no code fences, no explanation:
{
  "usable": true or false,
  "sufficient": true or false,
  "reason": "<one sentence explaining both decisions>"
}"""


def evaluate(state: AgentState) -> dict:
    last_tool = state["tool_attempts"][-1]
    last_result = state["tool_results"][last_tool]

    logger.info("[node:evaluate] Evaluating result from %s", last_tool)

    response = llm.invoke([
        SystemMessage(content=EVALUATE_SYSTEM),
        HumanMessage(content=(
            f"Tool: {last_tool}\n"
            f"Result:\n{last_result}"
        )),
    ])

    try:
        parsed = _parse_json(response.content)
        usable = bool(parsed["usable"])
        sufficient = bool(parsed.get("sufficient", True))
        reason = parsed["reason"]
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # If we can't parse the evaluation, assume usable to avoid infinite loops
        usable = True
        sufficient = True
        reason = "Could not parse evaluation response — treating as usable and sufficient."
        logger.warning("[node:evaluate] Failed to parse evaluation response (%s)", e)

    decision = "USABLE" if usable else "NOT USABLE"
    if usable and not sufficient:
        decision = "USABLE but NOT SUFFICIENT — need more data"

    trace_patch = _append_trace(
        state, "evaluate", f"{last_tool} result is {decision}", reason
    )

    failed_tools = list(state.get("failed_tools", []))
    if not usable:
        failed_tools = failed_tools + [last_tool]

    return {
        **trace_patch,
        "failed_tools": failed_tools,
        "_last_result_usable": usable and sufficient,
    }


def should_fallback(state: AgentState) -> str:
    """
    Conditional edge after evaluate.
    Routes to 'synthesize' if result was usable AND sufficient, or we've hit the attempt ceiling.
    Routes to 'fallback' if result was bad/insufficient and we have tools left to try.
    _last_result_usable is False when: result is unusable OR usable but not sufficient.
    """
    usable = state.get("_last_result_usable", True)
    attempts = len(state.get("tool_attempts", []))
    available = state.get("available_tools", [])

    if usable:
        return "synthesize"
    if attempts >= MAX_TOOL_ATTEMPTS or not available:
        logger.warning(
            "[edge:should_fallback] All options exhausted after %d attempts — forcing synthesize",
            attempts,
        )
        return "synthesize"
    return "fallback"


# ---------------------------------------------------------------------------
# Node: fallback
# LLM picks the next best tool from those not yet tried.
# ---------------------------------------------------------------------------

FALLBACK_SYSTEM = """You are deciding which tool to try next after a previous tool failed.

Available tools (not yet tried):
- get_build_logs: returns timestamped build step output
- get_test_results: returns structured JSON with pass/fail counts and error messages
- get_recent_commits: returns recent git commits — always reliable, never fails

Choose the most useful remaining tool given what you already know.
Respond with ONLY a raw JSON object — no markdown, no code fences, no explanation:
{
  "next_tool": "<tool_name>",
  "rationale": "<one sentence>"
}"""


def fallback(state: AgentState) -> dict:
    available = state.get("available_tools", [])
    failed = state.get("failed_tools", [])
    tool_results = state.get("tool_results", {})

    logger.info("[node:fallback] Choosing next tool from: %s", available)

    # Summarise what we already know to give LLM context
    known_results_summary = "\n".join([
        f"- {tool}: {'FAILED (unusable)' if tool in failed else 'SUCCESS'}"
        for tool in state.get("tool_attempts", [])
    ])

    response = llm.invoke([
        SystemMessage(content=FALLBACK_SYSTEM),
        HumanMessage(content=(
            f"Question: {state['question']}\n"
            f"Tools already tried:\n{known_results_summary}\n"
            f"Tools still available: {available}"
        )),
    ])

    try:
        parsed = _parse_json(response.content)
        next_tool = parsed["next_tool"]
        rationale = parsed["rationale"]
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # Default to first available tool
        next_tool = available[0] if available else "get_recent_commits"
        rationale = "Could not parse fallback response — defaulting to first available tool."
        logger.warning("[node:fallback] Failed to parse fallback response (%s)", e)

    # Validate chosen tool is actually available
    if next_tool not in available:
        next_tool = available[0] if available else "get_recent_commits"
        rationale = f"Suggested tool not available — defaulting to {next_tool}."

    # Remove chosen tool from available list
    new_available = [t for t in available if t != next_tool]

    trace_patch = _append_trace(
        state, "fallback", f"Switching to {next_tool}", rationale
    )

    return {
        **trace_patch,
        "available_tools": new_available,
        "_next_tool": next_tool,
    }


# ---------------------------------------------------------------------------
# Node: synthesize
# LLM produces final diagnosis using all usable data collected.
# ---------------------------------------------------------------------------

SYNTHESIZE_SYSTEM = """You are a senior DevOps engineer writing a concise incident diagnosis.

Using the tool results provided, produce:
1. Root cause: what specifically caused the build to fail
2. Evidence: which tools and what data support your conclusion
3. Confidence: "high" (multiple tools agree), "medium" (one good source), or "low" (all tools failed — commit history only)
4. Recommended action: the single most important next step for the team

If some tools failed and you are working with partial data, say so explicitly.
Be specific — mention file names, error messages, commit SHAs where available.

Respond with ONLY a raw JSON object — no markdown, no code fences, no explanation:
{
  "root_cause": "<specific root cause>",
  "evidence": "<what data supports this>",
  "confidence": "high" or "medium" or "low",
  "recommended_action": "<single most important next step>"
}"""


def synthesize(state: AgentState) -> dict:
    tool_results = state.get("tool_results", {})
    failed_tools = state.get("failed_tools", [])

    logger.info("[node:synthesize] Synthesizing diagnosis from %d tool results", len(tool_results))

    # Build a summary of what data we have vs what failed
    data_summary = []
    for tool_name, result in tool_results.items():
        status = "FAILED — unusable" if tool_name in failed_tools else "SUCCESS"
        data_summary.append(f"Tool: {tool_name} [{status}]\n{result}\n")

    response = llm.invoke([
        SystemMessage(content=SYNTHESIZE_SYSTEM),
        HumanMessage(content=(
            f"Original question: {state['question']}\n\n"
            f"Data collected:\n{'---'.join(data_summary)}"
        )),
    ])

    try:
        parsed = _parse_json(response.content)
        root_cause = parsed["root_cause"]
        evidence = parsed["evidence"]
        confidence = parsed["confidence"]
        recommended_action = parsed["recommended_action"]
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        root_cause = response.content
        evidence = "Could not parse structured response."
        confidence = "low"
        recommended_action = "Review raw tool output manually."
        logger.warning("[node:synthesize] Failed to parse synthesis response (%s)", e)

    trace_patch = _append_trace(
        state,
        "synthesize",
        f"Diagnosis complete (confidence: {confidence})",
        f"Used tools: {list(tool_results.keys())} | Failed: {failed_tools}",
    )

    return {
        **trace_patch,
        "diagnosis": root_cause,
        "confidence": confidence,
        "recommended_action": recommended_action,
    }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("plan", plan)
    graph.add_node("call_tool", call_tool)
    graph.add_node("evaluate", evaluate)
    graph.add_node("fallback", fallback)
    graph.add_node("synthesize", synthesize)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "call_tool")
    graph.add_edge("call_tool", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        should_fallback,
        {"synthesize": "synthesize", "fallback": "fallback"},
    )
    graph.add_edge("fallback", "call_tool")
    graph.add_edge("synthesize", END)

    return graph.compile()


# Compiled graph — imported by main.py
app = build_graph()
