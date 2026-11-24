"""The same agent as agent.py, built as a LangGraph state graph.

Why both: agent.py is the hand-written loop (about 100 lines, easy to read and stream).
This version shows what a framework adds when the flow gets more complex:

- **State with reducers.** `messages` and `retrieved` are lists that every node *appends* to
  (`operator.add`); LangGraph merges what each node returns into the state.
- **Conditional edges.** After the model speaks, `route` decides: answer, run read-only tools,
  or stop for a human because the model wants to write.
- **Human in the loop with `interrupt()`.** Before `create_access_request` runs, the graph
  pauses and returns what it wants to do. The user confirms or declines, and the graph resumes
  exactly there with `Command(resume=...)`. Nothing is written without that answer.
- **A checkpointer.** Every step is saved under a `thread_id` (the conversation id), which is
  what makes pausing and resuming possible and also gives the conversation memory. Here it is
  in memory; in production, `PostgresSaver` on the same database.

    START → model ─┬─ no tool call ────────────▶ finish → END
                   ├─ read-only tools ─────────▶ tools ──▶ model
                   ├─ a write (access request) ▶ confirm ─(interrupt)─▶ tools / model
                   └─ too many steps ──────────▶ give_up → END
"""

import json
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from platform_copilot import audit
from platform_copilot.agent import DEFAULT_TOOLS, resolve_citations, run_tool, system_prompt
from platform_copilot.llm.base import LLMProvider, Message, ToolCall, assistant_message
from platform_copilot.tools import create_access_request
from platform_copilot.tools.base import Tool, ToolContext

WRITE_TOOLS = {create_access_request.TOOL.name}
DECLINED = "The user declined this action. Do not retry it; tell the user it was not done."


class AgentState(TypedDict):
    messages: Annotated[list[Message], operator.add]  # reducer: nodes append, never replace
    retrieved: Annotated[list[str], operator.add]
    steps: int
    answer: str
    citations: list[dict[str, Any]]
    error: str


@dataclass(frozen=True)
class GraphResult:
    """What the caller gets back: an answer, or a write waiting for the user's confirmation."""

    answer: str
    citations: list[dict[str, Any]]
    pending: dict[str, Any] | None
    error: str


def build_graph(
    ctx: ToolContext,
    llm: LLMProvider,
    tools: dict[str, Tool] | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    if tools is None:
        tools = DEFAULT_TOOLS
    if checkpointer is None:
        checkpointer = InMemorySaver()
    schemas = [tool.schema() for tool in tools.values()]
    max_steps = ctx.settings.max_steps

    async def model(state: AgentState) -> dict[str, Any]:
        reply = await llm.chat(state["messages"], schemas)
        return {"messages": [assistant_message(reply)], "steps": state["steps"] + 1}

    def route(state: AgentState) -> str:
        calls = state["messages"][-1]["tool_calls"]
        if not calls:
            return "finish"
        if state["steps"] >= max_steps:
            return "give_up"
        if any(call.name in WRITE_TOOLS for call in calls):
            return "confirm"
        return "tools"

    def confirm(state: AgentState) -> Command[str]:
        calls: list[ToolCall] = state["messages"][-1]["tool_calls"]
        writes = [call for call in calls if call.name in WRITE_TOOLS]
        # Pauses the graph here. On resume, interrupt() returns the user's answer.
        approved = interrupt({"actions": [_describe(call) for call in writes]})
        if approved is True:
            return Command(goto="tools")
        declined = [_tool_message(call, {"error": DECLINED}, False) for call in writes]
        if len(writes) < len(calls):  # read-only calls in the same turn still run
            return Command(goto="tools", update={"messages": declined})
        return Command(goto="model", update={"messages": declined})

    async def run_tools(state: AgentState) -> dict[str, Any]:
        answered = {m["tool_call_id"] for m in state["messages"] if m["role"] == "tool"}
        results: list[Message] = []
        sources: list[str] = []
        for call in _last_calls(state["messages"]):
            if call.id in answered:
                continue  # declined writes already have their result
            outcome = await run_tool(call, ctx, tools)
            await audit.record(ctx.pool, ctx.user.id, ctx.conversation_id, "tool_call",
                               {"tool": call.name, "args": call.arguments, "ok": outcome.ok,
                                "engine": "langgraph"})  # fmt: skip
            results.append(_tool_message(call, outcome.content, outcome.ok))
            sources.extend(outcome.sources)
        return {"messages": results, "retrieved": sources}

    def finish(state: AgentState) -> dict[str, Any]:
        text = state["messages"][-1]["content"]
        answer, citations, _dropped = resolve_citations(text, state["retrieved"])
        return {"answer": answer, "citations": citations, "error": ""}

    def give_up(state: AgentState) -> dict[str, Any]:
        message = f"Stopped after {max_steps} steps without a final answer"
        return {"answer": "", "citations": [], "error": message}

    graph = StateGraph(AgentState)
    graph.add_node("model", model)
    graph.add_node("confirm", confirm)
    graph.add_node("tools", run_tools)
    graph.add_node("finish", finish)
    graph.add_node("give_up", give_up)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route, ["finish", "give_up", "confirm", "tools"])
    graph.add_edge("tools", "model")
    graph.add_edge("finish", END)
    graph.add_edge("give_up", END)
    return graph.compile(checkpointer=checkpointer)


async def ask(
    graph: CompiledStateGraph[Any, Any, Any, Any], ctx: ToolContext, question: str
) -> GraphResult:
    """A new question on the conversation's thread. Earlier turns come from the checkpointer."""
    config = _config(ctx)
    snapshot = await graph.aget_state(config)
    first_turn: list[Message] = []
    if not snapshot.values:
        first_turn = [{"role": "system", "content": system_prompt(ctx.user)}]
    await audit.record(ctx.pool, ctx.user.id, ctx.conversation_id, "question",
                       {"text": question, "engine": "langgraph"})  # fmt: skip
    state = {"messages": [*first_turn, {"role": "user", "content": question}], "steps": 0}
    return _result(await graph.ainvoke(state, config))


async def resume(
    graph: CompiledStateGraph[Any, Any, Any, Any], ctx: ToolContext, approved: bool
) -> GraphResult:
    """The user's answer to a pending write: the graph continues from the interrupt."""
    return _result(await graph.ainvoke(Command(resume=approved), _config(ctx)))


def _result(values: dict[str, Any]) -> GraphResult:
    interrupts = values.get("__interrupt__", [])
    if interrupts:
        return GraphResult("", [], interrupts[0].value, "")
    return GraphResult(values["answer"], values["citations"], None, values["error"])


def _config(ctx: ToolContext) -> Any:
    return {"configurable": {"thread_id": ctx.conversation_id}}


def _last_calls(messages: list[Message]) -> list[ToolCall]:
    """The tool calls of the last assistant message (declined writes may come after it)."""
    assistant = [message for message in messages if message["role"] == "assistant"]
    calls: list[ToolCall] = assistant[-1]["tool_calls"]
    return calls


def _describe(call: ToolCall) -> dict[str, Any]:
    return {"id": call.id, "tool": call.name, "args": call.arguments}


def _tool_message(call: ToolCall, content: dict[str, Any], ok: bool) -> Message:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": json.dumps(content, ensure_ascii=False, default=str),
        "is_error": not ok,
    }
