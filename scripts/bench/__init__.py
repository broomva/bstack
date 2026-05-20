"""bstack bench — skill-evolution benchmark substrate.

Empirical-validation primitive (P11) for agents in bstack-governed workspaces.
Two-phase protocol (Phase 1 cold → snapshot skills → Phase 2 warm) measures
whether evolved skills reduce token cost and/or improve task quality.

Spec: specs/bench-skill-evolution.md
Ticket: BRO-1205
"""

__all__ = ["orchestrator", "task_loader", "agent_runner", "evaluator"]
