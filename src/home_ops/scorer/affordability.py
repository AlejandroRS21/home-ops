"""Affordability scoring for property listings.

Computes the monthly mortgage payment using the standard Spanish
amortisation formula: principal × 0.80 over 30 years at euribor/12
monthly rate, then scores the payment-to-salary ratio against
configured thresholds.
"""

from __future__ import annotations

from decimal import Decimal


def score_affordability(
    price: Decimal | None,
    euribor_rate: float,
    salary_province: float,
    high_ratio: float = 0.50,
    medium_ratio: float = 0.30,
) -> float:
    """Score affordability based on monthly payment to salary ratio.

    Uses the standard Spanish mortgage formula:
        monthly = principal * 0.80 amortised over 30 years @ euribor/12

    Args:
        price: Listing price.
        euribor_rate: Annual Euribor rate (e.g. 3.5 for 3.5%).
        salary_province: Annual median salary for the province.
        high_ratio: Ratio above which score = 0.0 (unaffordable).
        medium_ratio: Ratio below which score = 1.0 (affordable).

    Returns:
        Score in [0.0, 1.0] where 1.0 = affordable (low payment-to-
        salary ratio), 0.0 = unaffordable (high ratio).
    """
    if price is None or price <= 0:
        return 0.0

    monthly_rate = (euribor_rate / 100.0) / 12.0
    principal = float(price) * 0.80
    n_payments = 360  # 30 years

    if monthly_rate <= 0:
        monthly_payment = principal / n_payments
    else:
        factor = (1 + monthly_rate) ** n_payments
        monthly_payment = (
            principal * monthly_rate * factor
        ) / (factor - 1)

    monthly_salary = salary_province / 12.0
    if monthly_salary <= 0:
        return 0.0

    ratio = monthly_payment / monthly_salary

    if ratio >= high_ratio:
        return 0.0
    if ratio <= medium_ratio:
        return 1.0
    return 1.0 - (ratio - medium_ratio) / (high_ratio - medium_ratio)
