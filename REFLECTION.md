# Reflection: Building with a Coding Assistant

I used Claude as my primary coding assistant throughout this task.

## Where it genuinely helped

The assistant was most useful for **scaffolding decisions I'd already made**. Once I had a clear scenario in mind — a CI diagnostics agent with a flaky tool that falls back — it translated that into a clean LangGraph structure quickly: five nodes, one conditional edge, a shared state schema. That structural work, which would normally take 30–45 minutes of LangGraph docs-reading and trial-and-error, took about 10 minutes. It also wrote all the mock data coherently — three builds with internally consistent failure stories where commit history, test results, and build logs all pointed to the same root cause.

It was less useful — and I leaned on it less — for **reasoning about agent behaviour**. The decision to make `evaluate` check both usability and sufficiency (not just whether data was readable) came from me running the agent and noticing it stopped too early. The assistant suggested the fix cleanly once I diagnosed the problem, but it didn't catch it upfront. Same with the `_next_tool` schema bug, it generated the inter-node communication field but didn't add it to `AgentState`, which LangGraph silently dropped. Both bugs only surfaced when I actually ran the code.

## What it got wrong

Two concrete issues. First, all four system prompts used the phrase "no other text" to request raw JSON, but the model still wrapped responses in markdown code fences. I caught this from the logs (`Failed to parse LLM response`) and fixed it two ways: a `_parse_json()` helper that strips fences defensively, and updated prompts that say "no markdown, no code fences" explicitly. Second, the `_next_tool` field was written into state but not declared in the `TypedDict` schema — LangGraph drops undeclared keys silently, so `call_tool` received `None` and failed. Neither bug was dangerous, but both required me to read the runtime output carefully rather than trust the generated code.

## Where I turned it off

I wrote the system prompts for each node myself and then iterated with the assistant. Prompts that instruct an LLM are sensitive, small wording changes produce meaningfully different behaviour and I wanted full control over what the agent was being asked to decide at each step. The `evaluate` prompt in particular went through three versions before the `usable AND sufficient` distinction was crisp enough to produce reliable routing.

## On using Claude to build a Claude-powered agent

It was recursive in a useful way. When the `plan` node's JSON parsing failed, I could ask the assistant "why would Claude return markdown-wrapped JSON when told not to?" and get a direct, accurate answer because it knows its own output tendencies. That made debugging faster than it would have been with a generic coding assistant.

## Traceability and observability

Every node appends a structured entry to `reasoning_trace` in `AgentState`. This captures the node name, the decision made, the rationale, and the tools tried and failed at that point. It prints as a readable timeline after each run. The design intent is that this trace could be shipped directly to LangSmith or a structured log sink in production — the format is already structured JSON, not free-form text. The `--seed` flag in `main.py` makes failure injection deterministic, which means any run can be reproduced exactly for debugging or eval.

## Evaluation approach

I'd assess this agent on three things: whether the final diagnosis matches ground truth across repeated runs (correctness), whether fallback runs — where the primary tool fails still produce a useful diagnosis (recovery rate), and whether the loop ceiling (`MAX_TOOL_ATTEMPTS = 3`) holds under adversarial LLM output (safety). In practice I'd run each build 20+ times with varying seeds, track confidence distribution, and flag any run that produces `confidence: low` when the data was available to do better. The ground truth for each scenario is known — so automated scoring is straightforward.
