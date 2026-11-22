from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MODEL_VERSION = "ridge-lags-v1"
LAG_WINDOW = 6
MIN_MODEL_OBSERVATIONS = 36


@dataclass(frozen=True)
class Forecast:
    method: str
    liters: list[float]


def _features(values: np.ndarray) -> list[float]:
    return [float(values[-1]), float(values[-2]), float(values[-3]), float(values[-6:].mean())]


def forecast_demand(values: np.ndarray, horizon: int) -> Forecast:
    if len(values) < MIN_MODEL_OBSERVATIONS:
        return Forecast("persistence", [float(values[-1])] * horizon)

    features = [_features(values[index - LAG_WINDOW:index]) for index in range(LAG_WINDOW, len(values))]
    model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    model.fit(features, values[LAG_WINDOW:])
    window = values[-LAG_WINDOW:].copy()
    predictions = []
    for _ in range(horizon):
        prediction = max(0.0, float(model.predict([_features(window)])[0]))
        predictions.append(prediction)
        window = np.append(window[1:], prediction)
    return Forecast("ridge", predictions)


def describe_demand(values: np.ndarray) -> dict:
    series = pd.Series(values, dtype=float)
    return {
        "observations": len(series),
        "mean_liters": float(series.mean()),
        "median_liters": float(series.median()),
        "peak_liters": float(series.max()),
        "latest_liters": float(series.iloc[-1]),
    }


def evaluate_forecast(values: np.ndarray, horizon: int) -> dict | None:
    split = max(MIN_MODEL_OBSERVATIONS, int(len(values) * 0.8))
    if len(values) - split < max(8, horizon):
        return None

    predicted = []
    baseline = []
    for start in range(split, len(values), horizon):
        size = min(horizon, len(values) - start)
        predicted.extend(forecast_demand(values[:start], size).liters)
        baseline.extend([float(values[start - 1])] * size)

    actual = values[split:]
    error = np.asarray(predicted) - actual
    return {
        "training_observations_at_first_origin": split,
        "evaluated_observations": len(actual),
        "horizon_ticks": horizon,
        "mae_liters": float(np.abs(error).mean()),
        "rmse_liters": float(np.sqrt(np.square(error).mean())),
        "persistence_mae_liters": float(np.abs(np.asarray(baseline) - actual).mean()),
    }
