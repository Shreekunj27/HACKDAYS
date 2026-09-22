from __future__ import annotations


def make_budget(total_carbon_budget_g: float) -> dict:
    return {
        "total_carbon_budget_g": round(total_carbon_budget_g, 6),
        "used_carbon_g": 0.0,
        "remaining_carbon_g": round(total_carbon_budget_g, 6),
    }


def deduct_carbon(budget: dict, carbon_g: float) -> dict:
    used = round(float(budget["used_carbon_g"]) + carbon_g, 6)
    total = float(budget["total_carbon_budget_g"])
    budget["used_carbon_g"] = used
    budget["remaining_carbon_g"] = round(max(0.0, total - used), 6)
    return budget
