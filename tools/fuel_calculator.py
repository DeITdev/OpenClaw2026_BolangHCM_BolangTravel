"""calculate_fuel_cost — local formula, no scraping.

Indonesian fuel prices are sticky (Pertamina announces changes periodically),
so we keep them in settings and let users override via .env. The agent calls
this purely deterministic function once it knows the total trip distance.
"""

from __future__ import annotations

import math

from config.settings import settings
from tools.schemas import FuelCostOutput

_PRICES = {
    "pertalite": lambda: settings.fuel_price_pertalite,
    "pertamax": lambda: settings.fuel_price_pertamax,
    "solar": lambda: settings.fuel_price_solar,
}


def calculate_fuel_cost(
    distance_km: float,
    fuel_type: str = "pertalite",
    consumption_km_per_liter: float | None = None,
) -> FuelCostOutput:
    fuel_key = fuel_type.lower().strip()
    if fuel_key not in _PRICES:
        fuel_key = "pertalite"

    consumption = consumption_km_per_liter or float(settings.default_fuel_consumption)
    price_per_liter = _PRICES[fuel_key]()

    liters_needed = distance_km / consumption if consumption > 0 else 0.0
    total_cost = int(math.ceil(liters_needed * price_per_liter))

    return FuelCostOutput(
        distance_km=distance_km,
        fuel_type=fuel_key,
        consumption_km_per_liter=consumption,
        liters_needed=round(liters_needed, 2),
        price_per_liter_idr=price_per_liter,
        total_cost_idr=total_cost,
    )
