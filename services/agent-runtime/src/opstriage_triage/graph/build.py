from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from opstriage_triage.graph import agents, nodes
from opstriage_triage.graph.state import TriageDeps, TriageState

TriageGraph = CompiledStateGraph[Any, Any, Any, Any]


def build_triage_graph(checkpointer: BaseCheckpointSaver[Any] | None) -> TriageGraph:
    g = StateGraph(TriageState, context_schema=TriageDeps)

    g.add_node("load_context", nodes.load_context)
    g.add_node("pre_route", nodes.pre_route)  # deterministic rules, may short-circuit
    g.add_node("classify", nodes.classify)  # decision model
    g.add_node("policy_gate", nodes.policy_gate)  # thresholds + autonomy -> plan
    g.add_node("notify", agents.notify_agent)
    g.add_node("ticket", agents.ticket_agent)
    g.add_node("escalate", agents.escalation_agent)
    g.add_node("remediate", agents.build_remediation_subgraph())  # guard -> approval -> execute -> verify
    g.add_node("resolve", agents.resolution_agent)  # CLOSED problems
    g.add_node("finalize", agents.finalize, defer=True)  # runs once after all selected branches

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "pre_route")
    g.add_conditional_edges(
        "pre_route", nodes.after_pre_route, ["classify", "policy_gate", "resolve", "finalize"]
    )
    g.add_edge("classify", "policy_gate")
    g.add_conditional_edges(
        "policy_gate", nodes.fan_out, ["notify", "ticket", "escalate", "remediate", "finalize"]
    )
    for branch in ("notify", "ticket", "escalate", "remediate", "resolve"):
        g.add_edge(branch, "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)
