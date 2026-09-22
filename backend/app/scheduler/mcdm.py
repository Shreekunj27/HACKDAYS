from __future__ import annotations

import math

from app.models import Candidate, PriorityProfile


CRITERIA = ["latency_ms", "accuracy", "cost_usd", "energy_wh", "carbon_g"]
LOWER_IS_BETTER = {"latency_ms", "cost_usd", "energy_wh", "carbon_g"}

PRIORITY_WEIGHTS: dict[PriorityProfile, dict[str, float]] = {
    "fast": {
        "latency_ms": 0.45,
        "accuracy": 0.2,
        "cost_usd": 0.12,
        "energy_wh": 0.1,
        "carbon_g": 0.13,
    },
    "balanced": {
        "latency_ms": 0.24,
        "accuracy": 0.26,
        "cost_usd": 0.18,
        "energy_wh": 0.14,
        "carbon_g": 0.18,
    },
    "green": {
        "latency_ms": 0.14,
        "accuracy": 0.2,
        "cost_usd": 0.12,
        "energy_wh": 0.2,
        "carbon_g": 0.34,
    },
    "quality": {
        "latency_ms": 0.16,
        "accuracy": 0.46,
        "cost_usd": 0.12,
        "energy_wh": 0.1,
        "carbon_g": 0.16,
    },
}


def _candidate_value(candidate: Candidate, criterion: str) -> float:
    return float(getattr(candidate, criterion))


def _normalize(values: list[float], lower_is_better: bool) -> list[float]:
    min_value = min(values)
    max_value = max(values)
    if math.isclose(min_value, max_value):
        return [1.0 for _ in values]

    span = max_value - min_value
    if lower_is_better:
        return [(max_value - value) / span for value in values]
    return [(value - min_value) / span for value in values]


def normalize_candidates(candidates: list[Candidate]) -> dict[str, dict[str, float]]:
    normalized = {candidate.candidate_id: {} for candidate in candidates}
    for criterion in CRITERIA:
        values = [_candidate_value(candidate, criterion) for candidate in candidates]
        normalized_values = _normalize(values, criterion in LOWER_IS_BETTER)
        for candidate, value in zip(candidates, normalized_values):
            normalized[candidate.candidate_id][criterion] = round(value, 6)
    return normalized


def entropy_weights(normalized_metrics: dict[str, dict[str, float]]) -> dict[str, float]:
    candidate_count = len(normalized_metrics)
    if candidate_count == 0:
        return {}
    if candidate_count == 1:
        return {criterion: round(1 / len(CRITERIA), 6) for criterion in CRITERIA}

    k = 1 / math.log(candidate_count)
    diversities: dict[str, float] = {}

    for criterion in CRITERIA:
        values = [metrics[criterion] for metrics in normalized_metrics.values()]
        total = sum(values)
        if math.isclose(total, 0):
            diversities[criterion] = 0.0
            continue

        entropy = 0.0
        for value in values:
            if value <= 0:
                continue
            proportion = value / total
            entropy -= k * proportion * math.log(proportion)
        diversities[criterion] = max(0.0, 1 - entropy)

    diversity_total = sum(diversities.values())
    if math.isclose(diversity_total, 0):
        return {criterion: round(1 / len(CRITERIA), 6) for criterion in CRITERIA}
    return {
        criterion: round(diversity / diversity_total, 6)
        for criterion, diversity in diversities.items()
    }


def blended_weights(
    entropy: dict[str, float],
    priority: PriorityProfile,
    alpha: float = 0.5,
) -> dict[str, float]:
    profile = PRIORITY_WEIGHTS[priority]
    raw = {
        criterion: (alpha * entropy.get(criterion, 0)) + ((1 - alpha) * profile[criterion])
        for criterion in CRITERIA
    }
    total = sum(raw.values())
    return {criterion: round(value / total, 6) for criterion, value in raw.items()}


def topsis_scores(
    normalized_metrics: dict[str, dict[str, float]],
    weights: dict[str, float],
) -> dict[str, float]:
    if not normalized_metrics:
        return {}

    ideal = {
        criterion: max(metrics[criterion] for metrics in normalized_metrics.values())
        for criterion in CRITERIA
    }
    anti_ideal = {
        criterion: min(metrics[criterion] for metrics in normalized_metrics.values())
        for criterion in CRITERIA
    }
    scores: dict[str, float] = {}

    for candidate_id, metrics in normalized_metrics.items():
        distance_to_ideal = math.sqrt(
            sum((weights[criterion] * (metrics[criterion] - ideal[criterion])) ** 2 for criterion in CRITERIA)
        )
        distance_to_anti = math.sqrt(
            sum((weights[criterion] * (metrics[criterion] - anti_ideal[criterion])) ** 2 for criterion in CRITERIA)
        )
        denominator = distance_to_ideal + distance_to_anti
        scores[candidate_id] = round(distance_to_anti / denominator if denominator else 1.0, 6)

    return scores


def balance_breakdown(
    selected: Candidate | None,
    normalized_metrics: dict[str, dict[str, float]],
    weights: dict[str, float],
    topsis_score: float | None,
) -> dict[str, object]:
    if selected is None or selected.candidate_id not in normalized_metrics:
        return {}

    selected_metrics = normalized_metrics[selected.candidate_id]
    weighted_contributions = {
        criterion: round(selected_metrics[criterion] * weights[criterion], 6)
        for criterion in CRITERIA
    }
    total_contribution = sum(weighted_contributions.values())
    regrets = {
        criterion: round(1.0 - selected_metrics[criterion], 6)
        for criterion in CRITERIA
    }
    contribution_values = list(weighted_contributions.values())
    mean_contribution = total_contribution / len(CRITERIA)
    if math.isclose(mean_contribution, 0.0):
        evenness_score = 0.0
    else:
        variance = sum((value - mean_contribution) ** 2 for value in contribution_values) / len(CRITERIA)
        evenness_score = max(0.0, 1.0 - (math.sqrt(variance) / mean_contribution))

    return {
        "criteria": CRITERIA,
        "direction": {
            criterion: "lower_is_better" if criterion in LOWER_IS_BETTER else "higher_is_better"
            for criterion in CRITERIA
        },
        "selected_candidate_id": selected.candidate_id,
        "raw_metrics": {criterion: _candidate_value(selected, criterion) for criterion in CRITERIA},
        "normalized_metrics": selected_metrics,
        "weights": weights,
        "weighted_contributions": weighted_contributions,
        "weighted_sum_score": round(total_contribution, 6),
        "topsis_score": topsis_score,
        "regret_from_ideal": regrets,
        "dominant_criterion": max(weighted_contributions, key=weighted_contributions.get),
        "largest_compromise": max(regrets, key=regrets.get),
        "balance_evenness_score": round(evenness_score, 6),
        "explanation": (
            "Higher weighted contributions help the selected plan; regret shows distance from the ideal normalized "
            "candidate on each metric."
        ),
    }


def rank_candidates(
    candidates: list[Candidate],
    priority: PriorityProfile,
    alpha: float = 0.5,
) -> tuple[Candidate | None, float | None, dict[str, float], dict[str, dict[str, float]], dict[str, float]]:
    if not candidates:
        return None, None, {}, {}, {}

    normalized = normalize_candidates(candidates)
    entropy = entropy_weights(normalized)
    weights = blended_weights(entropy, priority, alpha=alpha)
    scores = topsis_scores(normalized, weights)
    selected = max(candidates, key=lambda candidate: scores[candidate.candidate_id])
    return selected, scores[selected.candidate_id], weights, normalized, scores
