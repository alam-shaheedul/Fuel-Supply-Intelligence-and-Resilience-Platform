"""Run the resilience scenarios against an isolated Docker simulator and a real backend."""

import os
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import httpx
import uvicorn

from backend.app.decisions.execution import capacity_issue
from backend.app.decisions.models import ForecastInput, ForecastPoint, Snapshot
from backend.app.decisions.optimizer import build_plan
from backend.app.main import app

IMAGE = "asifmahmoud414/bup-fuel-supply-simulator:1.0.0"
STATION = "station-mirpur"
DEPOT = "depot-gazipur"


def docker(*arguments):
    return subprocess.check_output(["docker", *arguments], text=True).strip()


@contextmanager
def disposable_simulator():
    name = f"bup-crisis-{uuid4().hex[:10]}"
    docker(
        "run", "--rm", "-d", "--name", name, "-p", "127.0.0.1::8000",
        "-e", "TICK_MINUTES=15", "-e", "SIMULATOR_START_MODE=paused", IMAGE,
    )
    try:
        address = docker("port", name, "8000/tcp")
        with httpx.Client(base_url=f"http://{address}", timeout=3, trust_env=False) as simulator:
            for _ in range(100):
                try:
                    if simulator.get("/v1/health").is_success:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.3)
            else:
                raise RuntimeError("The disposable simulator did not become ready.")
            yield simulator
    finally:
        docker("stop", name)


@contextmanager
def backend(simulator):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=3))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    with patch.dict(os.environ, {"SIMULATOR_BASE_URL": str(simulator.base_url)}):
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("The test backend did not start.")
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(8)
            listener.close()


def get(client, path):
    return client.get(path).raise_for_status().json()


def step(simulator, times=1):
    for _ in range(times):
        simulator.post("/admin/step").raise_for_status()


def event(simulator, kind, parameters, duration=2):
    tick = get(simulator, "/v1/instance")["tick"]
    simulator.post("/admin/events", json={
        "type": kind, "start_tick": tick, "duration_ticks": duration, "parameters": parameters,
    }).raise_for_status()


def test_demand_spike(simulator, client):
    step(simulator)
    prior = get(client, f"/v1/stations/{STATION}")["demand_multiplier"]
    path = f"/v1/demand-history?station_id={STATION}&limit=30"
    before = max(row["demand_liters"] for row in get(client, path) if row["fuel_type"] == "DIESEL")
    event(simulator, "demand_spike", {"station_ids": [STATION], "multiplier": 3})
    step(simulator)
    assert get(client, f"/v1/stations/{STATION}")["demand_multiplier"] == prior * 3
    observed = max(row["demand_liters"] for row in get(client, path) if row["fuel_type"] == "DIESEL")
    assert observed > before * 2, (before, observed)
    step(simulator, 2)
    assert get(client, f"/v1/stations/{STATION}")["demand_multiplier"] == prior


def logistics(client):
    tick = get(client, "/v1/instance")["tick"]
    return Snapshot(tick=tick, **{
        name: get(client, f"/v1/{name}") for name in ("depots", "stations", "routes", "allocations")
    })


def proposed(snapshot):
    forecast = ForecastInput(id=1, station_id=STATION, fuel_type="DIESEL", points=[
        ForecastPoint(tick=tick, liters=2000) for tick in range(snapshot.tick, snapshot.tick + 12)
    ])
    return build_plan(snapshot, [forecast]).shipments


def test_route_disruption(simulator, client):
    baseline = proposed(logistics(client))
    original = next(item for item in baseline if item.source_depot_id == DEPOT)
    event(simulator, "route_disruption", {"route_ids": [original.route_id]})
    step(simulator)
    snapshot = logistics(client)
    assert next(item for item in snapshot.routes if item.id == original.route_id).status == "DISRUPTED"
    alternative = proposed(snapshot)
    assert alternative and all(item.route_id != original.route_id for item in alternative)
    assert capacity_issue(snapshot, [original])
    step(simulator, 2)
    assert next(item for item in logistics(client).routes if item.id == original.route_id).status == "AVAILABLE"


def test_depot_constraint(simulator, client):
    event(simulator, "depot_constraint", {"depot_ids": [DEPOT]})
    step(simulator)
    snapshot = logistics(client)
    depot = next(item for item in snapshot.depots if item.id == DEPOT)
    assert depot.status == "CONSTRAINED"
    shipments = proposed(snapshot)
    assert shipments and capacity_issue(snapshot, shipments) is None
    first = next(item for item in shipments if item.source_depot_id == DEPOT)
    assert capacity_issue(snapshot, [first] * (int(depot.dispatch_capacity_per_tick / first.quantity) + 1))
    step(simulator, 2)
    assert next(item for item in logistics(client).depots if item.id == DEPOT).status == "OPEN"


def test_shipment_delay(simulator, client):
    path = "/v1/supply-arrivals"
    current_tick = get(client, "/v1/instance")["tick"]
    arrival = min(
        (item for item in get(client, path) if item["planned_tick"] > current_tick),
        key=lambda item: item["planned_tick"],
    )
    event(simulator, "shipment_delay", {
        "depot_ids": [arrival["depot_id"]], "fuel_types": [arrival["fuel_type"]], "delay_ticks": 3,
    })
    step(simulator)
    tracked = next(item for item in get(client, path) if item["id"] == arrival["id"])
    assert tracked["planned_tick"] == arrival["planned_tick"] + 3
    assert tracked["status"] == "DELAYED"
    while get(client, "/v1/instance")["tick"] <= tracked["planned_tick"]:
        step(simulator)
    delivered = next(item for item in get(client, path) if item["id"] == arrival["id"])
    assert delivered["status"] == "ARRIVED" and delivered["actual_tick"] == tracked["planned_tick"]


@contextmanager
def fault(simulator, kind):
    simulator.post("/admin/faults", json={"type": kind, "duration_seconds": 60}).raise_for_status()
    try:
        yield
    finally:
        simulator.post("/admin/faults/clear").raise_for_status()


def assert_fault(response, code="FAULT_INJECTED"):
    assert response.status_code == 503 and response.json()["error"]["code"] == code, response.text


def test_api_unavailable(simulator, client):
    with fault(simulator, "unavailable"):
        assert_fault(client.get("/v1/depots"))
        assert_fault(client.post("/v1/recommendations", json={"prediction_ids": [1]}))
    assert get(client, "/v1/depots")


def test_stale_data(simulator, client):
    with fault(simulator, "stale_data"):
        response = client.get("/v1/stations").raise_for_status()
        assert response.headers["X-Simulator-Stale"].lower() == "true"
        assert_fault(client.post("/v1/recommendations", json={"prediction_ids": [1]}), "SIMULATOR_STALE_DATA")
        assert_fault(client.post("/v1/predictions", json={
            "run_id": "test", "station_id": STATION, "fuel_type": "DIESEL",
        }), "SIMULATOR_STALE_DATA")
    assert "X-Simulator-Stale" not in client.get("/v1/stations").raise_for_status().headers


def test_stream_disconnect(simulator, client):
    with fault(simulator, "stream_disconnect"):
        assert_fault(client.get("/v1/stream"))
        assert get(client, "/v1/instance")
    with client.stream("GET", "/v1/stream") as response:
        response.raise_for_status()
        lines = response.iter_lines()
        assert next(lines) == ": connected"
        step(simulator)
        for line in lines:
            if line == "event: simulation.tick":
                break
        else:
            raise AssertionError("The recovered stream did not deliver a tick event.")


def main():
    with disposable_simulator() as simulator, backend(simulator) as client:
        for scenario in (
            test_demand_spike, test_route_disruption, test_depot_constraint, test_shipment_delay,
            test_api_unavailable, test_stale_data, test_stream_disconnect,
        ):
            scenario(simulator, client)
            print(f"PASS {scenario.__name__.removeprefix('test_').replace('_', ' ')}", flush=True)


if __name__ == "__main__":
    main()
