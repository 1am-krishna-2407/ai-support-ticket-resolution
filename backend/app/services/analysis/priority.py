"""Deterministic priority rubric (docs/priority-rubric.md).

The LLM only extracts signals from the ticket; this function maps them to a level, so the
same signals always give the same priority and every decision can be explained.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.analysis import ImpactLevel, PriorityLevel, PrioritySignals, UsersAffected

_WIDE = {UsersAffected.MANY, UsersAffected.ALL}


@dataclass(frozen=True)
class PriorityDecision:
    level: PriorityLevel
    triggered: tuple[str, ...]
    explanation: str


def assess_priority(s: PrioritySignals) -> PriorityDecision:
    outage = s.production_impact is ImpactLevel.OUTAGE
    degraded = s.production_impact is ImpactLevel.DEGRADED
    wide = s.users_affected in _WIDE

    security, data_loss = s.security_concern, s.data_loss_risk
    blocked, workaround = s.blocks_ci_cd_or_deployments, s.workaround_available
    beyond_single_user = s.users_affected is not UsersAffected.SINGLE
    critical, high, medium = PriorityLevel.CRITICAL, PriorityLevel.HIGH, PriorityLevel.MEDIUM

    rules: list[tuple[PriorityLevel, str, bool]] = [
        (critical, "production outage affecting many or all users", outage and wide),
        (critical, "security concern with production impact", security and (outage or degraded)),
        (critical, "risk of data loss during a production outage", data_loss and outage),
        (high, "production outage", outage),
        (high, "security concern", security),
        (high, "risk of data loss", data_loss),
        (high, "CI/CD or deployments blocked with no workaround", blocked and not workaround),
        (
            high,
            "degraded service for many users with no workaround",
            degraded and wide and not workaround,
        ),
        (medium, "degraded production service", degraded),
        (medium, "CI/CD or deployments blocked (workaround exists)", blocked),
        (medium, "affects a team or more", beyond_single_user),
        (medium, "time-sensitive", s.time_sensitive),
        (medium, "regression of previously working behaviour", s.regression),
    ]

    for level in (PriorityLevel.CRITICAL, PriorityLevel.HIGH, PriorityLevel.MEDIUM):
        triggered = tuple(reason for lvl, reason, hit in rules if lvl is level and hit)
        if triggered:
            return PriorityDecision(
                level, triggered, f"{level.value.capitalize()}: " + "; ".join(triggered) + "."
            )
    reason = "single user, no production impact" + (
        ", workaround available" if s.workaround_available else ""
    )
    return PriorityDecision(PriorityLevel.LOW, (reason,), f"Low: {reason}.")
