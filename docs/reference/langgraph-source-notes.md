# LangGraph / LangSmith Source Notes

## Purpose

This file is the project-local source note for LangGraph, LangSmith Studio, and related implementation conventions.

Use this file before answering repeated project questions about:

- LangGraph node naming
- conditional routing
- graph visualization
- LangGraph Studio versus VS Code
- LangSmith tracing and local debugging
- which sources are authoritative

This file is not a replacement for official documentation. It is a project memory layer that records what has already been checked and where the source came from.

## Source priority

Use sources in this order:

1. Official LangChain / LangGraph / LangSmith documentation.
2. Official Python documentation.
3. LangGraph GitHub source or docstrings when the public docs are unclear.
4. LangChain Academy material as learning/reference examples only.
5. Blog posts, videos, forum answers, or AI answers only as secondary clues, not source of truth.

## Important distinction: Academy material

LangChain Academy material in the local file system is useful for learning concepts and seeing small examples.

It should not be treated as the production architecture source of truth.

Academy notebooks and screenshots are reference examples for learning:

- state
- nodes
- edges
- conditional edges
- graph invocation
- Studio screenshots

They are not proof that a pattern is the best production pattern for this project.

For production-style decisions, prefer official docs and source-level references.

## Official LangChain docs landing pages

Official sources:

- https://docs.langchain.com/
- https://docs.langchain.com/oss/python/langgraph/overview

Checked notes from the docs landing page:

- LangChain describes itself as a platform for agent engineering.
- LangSmith includes observability, evaluation, prompt engineering, deployment, and related platform features.
- LangGraph is listed under open source agent frameworks.
- The docs describe LangGraph as giving control over every step of a custom agent with low-level orchestration, memory, and human-in-the-loop support.

Checked notes from the LangGraph overview:

- LangGraph is described as a low-level orchestration framework and runtime for building, managing, and deploying long-running, stateful agents.
- LangGraph can be used without LangChain.
- The docs commonly use LangChain components for models/tools, but LangChain is not required to use LangGraph.
- LangGraph focuses on capabilities important for agent orchestration, including durable execution, streaming, human-in-the-loop, persistence, memory, and debugging with LangSmith.
- The ecosystem distinction in the overview is:
  - Deep Agents: agent harness on top of LangGraph.
  - LangChain: agent framework and integrations for models/tools/agent loops.
  - LangGraph: orchestration runtime.
  - LangSmith: platform for tracing, evaluation, prompts, and deployment.

Project interpretation:

- These official pages support the project direction of learning LangGraph as the low-level orchestration layer.
- They also support keeping LangSmith/Studio as debugging/observability tooling rather than treating it as the code editor.
- They do not settle production code-style choices such as strings versus enums for node names.

## Academy Lesson 1: Motivation / ecosystem context

Source provided by user:

- `LangChain Academy - Introduction to LangGraph - Motivation.pdf`
- Section: Lesson 1: Motivation / LangGraph FAQ

This material is useful for understanding the LangChain ecosystem and why LangGraph exists.

Key notes from the Academy FAQ:

- LangGraph can be used without LangChain.
- LangGraph is an orchestration framework for complex agentic systems.
- LangGraph is lower-level and more controllable than LangChain agents.
- LangChain provides standard interfaces for models and components and is useful for straightforward chains and retrieval flows.
- LangGraph is positioned for company-specific complex workflows where generic black-box agent architectures are not enough.
- LangGraph is MIT-licensed open source and free to use.
- LangSmith Deployment is proprietary and separate from open-source LangGraph.
- LangGraph provides low-level orchestration, memory, human-in-the-loop support, and durable execution.
- LangSmith provides observability, evaluation, and deployment capabilities.

Project interpretation:

- This supports the project direction: use LangGraph concepts for controlled orchestration rather than relying on a black-box agent framework.
- Academy material is valid for conceptual motivation and ecosystem vocabulary.
- Academy material should still not be treated as the production pattern source for code style choices such as node naming conventions, enums, deployment architecture, or project structure.

## VS Code versus LangGraph Studio

Current understanding:

- VS Code is the development environment for editing, running, testing, and committing code.
- LangGraph Studio is a browser-based visual interface/debugging UI for locally running LangGraph/LangChain agents.
- Studio does not replace VS Code.
- Studio connects to a local LangGraph API server.

Official source:

- LangSmith Studio docs: https://docs.langchain.com/oss/python/langgraph/studio
- Local server docs: https://docs.langchain.com/oss/python/langgraph/local-server

Relevant source notes:

- LangSmith Studio is described as a free visual interface for developing and testing LangChain agents from a local machine.
- Studio connects to a locally running agent and shows steps, prompts, tool calls, results, and final output.
- Studio can inspect intermediate states and help debug issues.
- `langgraph dev` starts a local server and prints a Studio URL such as `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`.
- The local server docs describe Studio as a specialized UI to visualize, interact with, and debug a LangGraph application locally.

Project implication:

- Keep using VS Code / WSL for code.
- Use Studio as the confirmed visual debugger for the project graph exposed through `langgraph dev`.
- Do not assume OpenAI API is required for the current graph. The current graph has no LLM node. Studio/LangSmith may require a LangSmith API key, but model keys are only needed when graph nodes call a model.

## LangGraph Studio setup source notes

Official source:

- https://docs.langchain.com/oss/python/langgraph/studio
- https://docs.langchain.com/oss/python/langgraph/local-server

Checked facts:

- Python >= 3.11 is required for the LangGraph CLI.
- Install command shown by docs: `pip install --upgrade "langgraph-cli[inmem]"`.
- For this project, the confirmed uv command is: `uv add --dev "langgraph-cli[inmem]"`.
- Studio setup expects a `langgraph.json` config file that tells the CLI where the graph object is.
- The `graphs` key points to a Python module path and object, for example `./src/agent.py:agent` in the docs.
- `langgraph dev` runs the local development server.
- The local server is for development/testing; production deployment is separate.

Project implication:

- Studio uses the existing `langgraph.json` file and the compiled `graph` object exposed from `./src/ai_tech_lead/brief_graph.py:graph`.
- Do not create duplicate projects just to use Studio.
- The current Studio graph name is `brief_graph`.

## Node names: strings, constants, and StrEnum

There are three separate concepts:

```python
workflow.add_node("node_name", node_function)
```

- First argument: LangGraph node name.
- Second argument: Python function/action to run.

Example:

```python
workflow.add_node("approval_required", approval_required_node)
```

This means:

- Register a graph node named `approval_required`.
- When that node runs, call `approval_required_node()`.

### Direct strings

Official LangGraph learning examples commonly use direct strings for node names.

Source:

- https://docs.langchain.com/oss/python/langgraph/workflows-agents

Project interpretation:

- Direct strings are fine for learning and small graphs.
- They are the clearest when learning LangGraph basics.
- They are not necessarily the best maintainability pattern for a growing app.

### Constants

Python constants such as this are a normal maintainability pattern:

```python
READ_REQUEST = "read_request"
```

Project interpretation:

- Constants reduce repeated string typing.
- Constants are useful once the graph is stable.
- Constants can confuse beginners because there are now three names to track:
  - constant variable name, e.g. `READ_REQUEST`
  - string value, e.g. `"read_request"`
  - function name, e.g. `read_request_node`

### StrEnum

A professional option is Python `StrEnum`:

```python
from enum import StrEnum

class NodeName(StrEnum):
    READ_REQUEST = "read_request"
    ASSESS_RISK = "assess_risk"
    CREATE_BRIEF = "create_brief"
    APPROVAL_REQUIRED = "approval_required"
    END_NODE = "end_node"
```

Official Python source:

- https://docs.python.org/3/library/enum.html

Source note:

- `StrEnum` is part of Python's enum support and creates enum members that are also strings.

Project interpretation:

- `StrEnum` is a reasonable maintainable pattern for project code.
- It is not required for early learning.
- It may be preferable once the graph has enough nodes that repeated strings become error-prone.

## Conditional routing: decision node return value

A conditional routing function returns the next route key / node name depending on state.

Example without enums:

```python
def decision_node(state: GraphState) -> str:
    if state["needs_approval"]:
        return "approval_required"
    return "end_node"
```

The return value is a string, not a function.

It does not return `approval_required_node`.

It returns the graph node name, such as `"approval_required"`.

## Conditional routing and diagrams

Problem observed:

- If a conditional function is typed only as `-> str`, the graph may run, but the visual diagram may not know the possible destinations.
- The diagram can show floating nodes or unclear conditional paths.

Official/source-level rule to verify when needed:

- `add_conditional_edges` can infer possible branches from return type hints such as `Literal[...]`, or from an explicit `path_map`.
- Without either, visualization may assume the conditional edge could go to many or all nodes.

Useful source targets:

- LangGraph docs: https://docs.langchain.com/oss/python/langgraph/workflows-agents
- LangGraph source/docstring for `add_conditional_edges`: https://github.com/langchain-ai/langgraph

Project preference:

Use `path_map` when we want readable code and a correct diagram without fighting `Literal` and constants.

Example:

```python
workflow.add_conditional_edges(
    NodeName.CREATE_BRIEF,
    decision_node,
    {
        "approval_required": NodeName.APPROVAL_REQUIRED,
        "end_node": NodeName.END_NODE,
    },
)
```

Or without `StrEnum`:

```python
workflow.add_conditional_edges(
    "create_brief",
    decision_node,
    {
        "approval_required": "approval_required",
        "end_node": "end_node",
    },
)
```

## Literal and constants

`Literal[...]` is a Python typing feature.

Official Python source:

- https://docs.python.org/3/library/typing.html

Practical note:

- Type checkers expect literal values inside `Literal[...]`, for example:

```python
Literal["approval_required", "end_node"]
```

- Do not expect this to work cleanly with plain constants:

```python
Literal[APPROVAL_REQUIRED, END_NODE]
```

Project preference:

- During learning: avoid `Literal` unless it is necessary for diagram inference.
- Prefer explicit `path_map` for diagram clarity.
- If using `Literal`, write literal string values directly and accept the duplication.

## Current project learning interpretation

Current graph concepts:

- `request`: the user input text being passed through graph state.
- `brief`: the generated execution brief for a coding agent or future workflow.
- `needs_approval`: deterministic safety flag based on request risk.

Current design direction:

- `read_request_node` is currently mostly a debug/start node.
- It does not yet add meaningful business value unless it validates, normalizes, or records the request.
- A future real version of `read_request_node` should do one of:
  - strip/normalize input
  - reject empty requests
  - preserve original request separately
  - add request source metadata such as `source="telegram"`
  - create a request id

Honest status:

- If `read_request_node` only prints and returns state unchanged, it is useful for learning and debugging but not necessary in production.

## Lessons learned from project setup session

These notes were added after mistakes in the assistant guidance during the LangGraph Studio setup and graph-routing exercise.

### Do not answer code-specific questions from memory

When the user asks about current code, inspect the current file first.

Do not infer from a previous version of the file. The project changed quickly during the session, and advice based on stale code caused confusion.

### Do not conflate these three things

Keep these separate:

1. A graph node name: the string or `StrEnum` value LangGraph uses to identify a node.
2. A Python function: the callable that runs when a node executes.
3. A routing function: a function used by `add_conditional_edges` to choose the next route.

Example:

```python
workflow.add_node(NodeNames.APPROVAL_REQUIRED, approval_required_node)
```

- `NodeNames.APPROVAL_REQUIRED` is the node name.
- `approval_required_node` is the function that runs.

A conditional routing function like `decision_node` may not appear as a visible graph node if it is used only inside `add_conditional_edges`.

If it is also registered with `workflow.add_node(...)`, it will show as a separate node and may float in Studio if no normal edges connect it.

### Build function versus runner function

For LangGraph Studio, expose a compiled graph object:

```python
def build_graph():
    workflow = StateGraph(GraphState)
    # add nodes and edges
    return workflow.compile()


graph = build_graph()
```

For local terminal testing, use a separate runner:

```python
def run_sample_graph() -> None:
    app = build_graph()
    app.invoke({...})
```

Do not put test invocations inside `build_graph()`.

### Infinite loop lesson

This creates an infinite loop:

```python
workflow.add_edge(NodeNames.END_NODE, NodeNames.END_NODE)
```

It means:

```text
end_node -> end_node -> end_node -> forever
```

The end node should normally point to LangGraph's special `END` marker:

```python
workflow.add_edge(NodeNames.END_NODE, END)
```

### LangGraph Studio diagram lesson

The diagram is useful for catching graph wiring mistakes:

- A node with an arrow to itself indicates a loop.
- A floating node often means it was registered as a normal node but no graph edge reaches it.
- Conditional routing functions do not necessarily appear as normal nodes; they can be invisible routing logic between nodes.

### Current local convention

For this project, the current preferred pattern is:

- Use `StrEnum` for node names once the learner is comfortable with node/function distinction.
- Use a separate Python function for each graph node.
- Use a separate routing function for conditional edges.
- Use `path_map` if Studio visualization needs explicit routing information and `Literal` becomes confusing.
- Keep Academy examples as learning references, but verify production or maintainability decisions against official docs/source.

## Project rule for future assistant sessions

Before answering LangGraph/LangSmith architecture questions for this project:

1. Check this file first.
2. Check current project code before advising on code-specific errors.
3. If the answer depends on current LangGraph behavior, verify with official docs/source before answering.
4. Do not treat Academy examples as production source of truth.
5. Do not recommend broad rewrites while the user is learning a single concept.
6. When the user asks a narrow question, answer that exact narrow question first.
7. Do not say probably when the answer can be verified from current code or official docs.
