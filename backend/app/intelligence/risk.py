import numpy as np


def detect_latest_anomaly(values: np.ndarray) -> dict:
    reference = values[-49:-1]
    if len(reference) < 12:
        return {"status": "insufficient_history"}

    median = float(np.median(reference))
    mad = float(np.median(np.abs(reference - median)))
    # The floor avoids flagging tiny movements in nearly constant series.
    scale = max(1.4826 * mad, median * 0.05, 0.1)
    score = float((values[-1] - median) / scale)
    return {
        "status": "anomaly" if abs(score) >= 3.5 else "normal",
        "reference_observations": len(reference),
        "reference_median_liters": median,
        "robust_score": score,
        "threshold": 3.5,
    }


def project_shortage(inventory: float, forecasts: list[float], first_tick: int) -> dict:
    cumulative = np.cumsum(forecasts)
    shortages = np.flatnonzero(cumulative > inventory)
    return {
        "starting_inventory_liters": inventory,
        "first_shortage_tick": first_tick + int(shortages[0]) if len(shortages) else None,
        "projected_unmet_liters": max(0.0, float(cumulative[-1]) - inventory),
        "projected_ending_inventory_liters": max(0.0, inventory - float(cumulative[-1])),
        "assumption": "no_replenishment",
    }
