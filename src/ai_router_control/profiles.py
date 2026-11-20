"""Profiles: named billing + access boundaries for ai-router (Phase 5).

A profile maps to one LiteLLM Team. The control plane owns profile
definitions (name, budgets, model allowlist, limits) and syncs them into
LiteLLM; LiteLLM enforces budgets and model access at request time.
This module is pure domain logic: no I/O, no LiteLLM calls.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass


PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Budget alert thresholds, as fractions of the monthly budget.
BUDGET_ALERT_THRESHOLDS = (0.5, 0.8, 1.0)

# Token anomaly: today's tokens above this multiple of the 7-day daily mean.
TOKEN_ANOMALY_MULTIPLE = 2.0
# Days of history required before an anomaly alert can fire.
TOKEN_ANOMALY_MIN_HISTORY_DAYS = 3


@dataclass(frozen=True, slots=True)
class ProfileDefinition:
    id: str
    name: str
    description: str
    monthly_budget_usd: float | None
    gpu_budget_usd: float | None
    rpm_limit: int | None
    tpm_limit: int | None
    allowed_models: tuple[str, ...]


# Starter budgets are deliberately modest placeholders. The owner adjusts
# them with PATCH /control/v1/profiles/{id}; a null budget means uncapped.
STARTER_PROFILES: tuple[ProfileDefinition, ...] = (
    ProfileDefinition(
        id="personal",
        name="Personal",
        description="Own experiments, learning, and side tools.",
        monthly_budget_usd=50.0,
        gpu_budget_usd=25.0,
        rpm_limit=60,
        tpm_limit=100_000,
        allowed_models=("coding/*", "local/*", "system/*"),
    ),
    ProfileDefinition(
        id="aayvu",
        name="Aayvu",
        description="Aayvu market-research startup. Spend stays separable for accounting and the 50/50 co-founder split.",
        monthly_budget_usd=100.0,
        gpu_budget_usd=50.0,
        rpm_limit=120,
        tpm_limit=200_000,
        allowed_models=("coding/*", "local/*"),
    ),
    ProfileDefinition(
        id="splitllm",
        name="SplitLLM",
        description="SplitLLM expense-splitting app.",
        monthly_budget_usd=50.0,
        gpu_budget_usd=25.0,
        rpm_limit=60,
        tpm_limit=100_000,
        allowed_models=("coding/*", "local/*"),
    ),
    ProfileDefinition(
        id="everything-else",
        name="Everything else",
        description="Catch-all for anything not covered by a named profile. Never the default for a known app.",
        monthly_budget_usd=25.0,
        gpu_budget_usd=10.0,
        rpm_limit=30,
        tpm_limit=50_000,
        allowed_models=("coding/fast", "local/*"),
    ),
)

STARTER_PROFILE_IDS = tuple(profile.id for profile in STARTER_PROFILES)


def validate_profile_id(value: str) -> str:
    normalized = value.strip().lower()
    if not PROFILE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("profile id must be lowercase letters, digits, - or _, up to 64 chars")
    return normalized


def model_allowed(allowlist: list[str], model: str) -> bool:
    """Glob match: ``coding/*`` allows ``coding/fast`` but not ``local/x``."""
    return any(fnmatch.fnmatchcase(model, pattern) for pattern in allowlist)


def disallowed_models(allowlist: list[str], models: list[str]) -> list[str]:
    return [model for model in models if not model_allowed(allowlist, model)]


def migrate_key_tags(tags: list[str], key_alias: str) -> tuple[str, bool]:
    """Map a legacy LiteLLM key's tags/alias to a profile.

    Returns (profile_id, unmatched). Unmatched keys go to
    ``everything-else`` with the flag set so the dashboard can show them
    visibly — never silently to ``personal``.
    """
    haystack = " ".join([*tags, key_alias]).lower()
    if re.search(r"\baayvu\b", haystack):
        return "aayvu", False
    if re.search(r"\b(splitllm|split-llm|split)\b", haystack):
        return "splitllm", False
    if re.search(r"\b(personal|dev|me)\b", haystack):
        return "personal", False
    return "everything-else", True


def budget_alert_kinds(monthly_budget_usd: float | None, month_spend_usd: float) -> list[str]:
    """Budget thresholds crossed by current month spend, e.g. ['budget_50']."""
    if monthly_budget_usd is None or monthly_budget_usd <= 0:
        return []
    fraction = month_spend_usd / monthly_budget_usd
    return [
        f"budget_{int(threshold * 100)}"
        for threshold in BUDGET_ALERT_THRESHOLDS
        if fraction >= threshold
    ]


def token_anomaly(daily_tokens: list[tuple[str, int]]) -> tuple[bool, float | None]:
    """Detect abnormal token volume: today above 2x the 7-day daily mean.

    ``daily_tokens`` is a list of (day, tokens) sorted oldest-first, with
    the last entry being today. Returns (is_anomaly, multiple). Needs at
    least TOKEN_ANOMALY_MIN_HISTORY_DAYS of baseline history; otherwise
    returns (False, None) rather than guessing.
    """
    if len(daily_tokens) < TOKEN_ANOMALY_MIN_HISTORY_DAYS + 1:
        return False, None
    baseline = [tokens for _, tokens in daily_tokens[-8:-1]]
    if len(baseline) < TOKEN_ANOMALY_MIN_HISTORY_DAYS:
        return False, None
    mean_baseline = sum(baseline) / len(baseline)
    today = daily_tokens[-1][1]
    if mean_baseline <= 0:
        return (today > 0), None
    multiple = today / mean_baseline
    return multiple >= TOKEN_ANOMALY_MULTIPLE, multiple


def burn_rate(
    daily_spend: list[tuple[str, float]], day_of_month: int, days_in_month: int
) -> tuple[float | None, float | None]:
    """(daily burn rate, projected month-end spend) from daily spend rows.

    Returns (None, None) when there is not enough data to project honestly.
    """
    if not daily_spend or day_of_month < 1 or days_in_month < 1:
        return None, None
    total = sum(amount for _, amount in daily_spend)
    days_observed = len(daily_spend)
    if days_observed < 1:
        return None, None
    rate = total / days_observed
    return rate, rate * days_in_month
