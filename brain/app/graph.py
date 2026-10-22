from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.nodes.context import context_node
from app.nodes.critic import critic_node
from app.nodes.deliver import deliver_node, react_node
from app.nodes.gate import gate_node
from app.nodes.mood import mood_node
from app.nodes.roast import write_node

if TYPE_CHECKING:
    from app.runtime import Runtime


class Turn(TypedDict, total=False):
    group_id: int
    now: datetime
    started: float
    forced: dict[str, Any] | None
    mood: str
    addressed: bool
    decision: dict[str, Any]
    context: dict[str, Any]
    candidates: list[dict[str, Any]]
    chosen: dict[str, Any] | None
    outcome: str


def route_gate(st: Turn) -> str:
    return {"SKIP": END, "REACT": "react", "ACK": "deliver"}.get(st["decision"]["action"], "context")


def route_critic(st: Turn) -> str:
    return "deliver" if st.get("chosen") else END


def build_graph(rt: "Runtime"):
    def bind(fn: Callable[["Runtime", Turn], Awaitable[dict]]):
        async def node(state: Turn) -> dict:
            return await fn(rt, state)

        return node

    g = StateGraph(Turn)
    g.add_node("mood", bind(mood_node))
    g.add_node("gate", bind(gate_node))
    g.add_node("react", bind(react_node))
    g.add_node("context", bind(context_node))
    g.add_node("write", bind(write_node))
    g.add_node("critic", bind(critic_node))
    g.add_node("deliver", bind(deliver_node))

    g.add_edge(START, "mood")
    g.add_edge("mood", "gate")
    g.add_conditional_edges("gate", route_gate, ["react", "context", "deliver", END])
    g.add_edge("react", END)
    g.add_edge("context", "write")
    g.add_edge("write", "critic")
    g.add_conditional_edges("critic", route_critic, ["deliver", END])
    g.add_edge("deliver", END)
    return g.compile()
