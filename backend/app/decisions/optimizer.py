from collections import defaultdict
from dataclasses import dataclass
from math import floor, fsum

from ortools.sat.python import cp_model

from .execution import active_reservations
from .models import ForecastInput, Plan, Route, Shipment, Snapshot, StationAssessment


class OptimizationError(Exception):
    pass


@dataclass
class Candidate:
    route: Route
    forecast: ForecastInput
    arrival_tick: int
    limit: int
    variable: cp_model.IntVar


def estimate_arrival(current_tick: int, transit_ticks: int) -> int:
    return current_tick + transit_ticks


def build_plan(snapshot: Snapshot, forecasts: list[ForecastInput]) -> Plan:
    model = cp_model.CpModel()
    depots = {depot.id: depot for depot in snapshot.depots}
    stations = {station.id: station for station in snapshot.stations}
    routes = {route.id: route for route in snapshot.routes}
    dispatch_reserved, tank_reserved = active_reservations(snapshot)
    scheduled_arrivals = defaultdict(float)
    candidates = []
    assessments = {}

    for allocation in snapshot.allocations:
        if allocation.status not in {"PENDING", "IN_TRANSIT"}:
            continue
        key = (allocation.destination_station_id, allocation.fuel_type)
        route = routes.get(allocation.route_id)
        arrival_tick = allocation.expected_arrival_tick
        if arrival_tick is None and route and allocation.status == "PENDING" and route.status == "AVAILABLE":
            arrival_tick = estimate_arrival(snapshot.tick, route.transit_ticks)
        if arrival_tick is not None:
            scheduled_arrivals[key, max(snapshot.tick, arrival_tick)] += allocation.quantity

    dispatch = {
        depot.id: max(0, floor(depot.dispatch_capacity_per_tick - dispatch_reserved[depot.id]))
        for depot in snapshot.depots
    }

    for forecast in forecasts:
        station = stations[forecast.station_id]
        fuel = forecast.fuel_type
        key = (station.id, fuel)
        stock = station.inventory[fuel]
        tank = station.capacity[fuel]
        room = max(0, floor(tank - stock - tank_reserved[key]))
        deficits = {}
        for point in forecast.points:
            stock = min(tank, stock + scheduled_arrivals[key, point.tick])
            deficits[point.tick] = max(0.0, point.liters - stock)
            stock = max(0.0, stock - point.liters)

        need = fsum(deficits.values())
        first = next((tick for tick, amount in deficits.items() if amount > 0), None)
        demand = fsum(point.liters for point in forecast.points)
        priority = 0 if first is None else 100 + 10 * (24 - (first - snapshot.tick)) + round(100 * need / demand)
        assessment = StationAssessment(
            prediction_id=forecast.id, station_id=station.id, fuel_type=fuel,
            first_shortage_tick=first, projected_unmet_liters=need, priority_score=priority,
            inbound_liters=tank_reserved[key], available_tank_space_liters=room,
            explanation="The forecast projects no shortage after scheduled arrivals.",
        )
        assessments[forecast.id] = assessment
        if station.status != "OPEN":
            assessment.explanation = "The station is unavailable for deliveries."
        elif 0 < need < 1:
            assessment.explanation = "The projected shortage is below one liter."
        elif need >= 1 and room == 0:
            assessment.explanation = "Current stock and inbound shipments leave no whole-liter tank space."
        elif need >= 1:
            choices = []
            for route in sorted(routes.values(), key=lambda item: item.id):
                if route.destination_station_id != station.id or route.status != "AVAILABLE":
                    continue
                depot = depots.get(route.source_depot_id)
                if depot is None or depot.status not in {"OPEN", "CONSTRAINED"}:
                    continue
                arrival = estimate_arrival(snapshot.tick, route.transit_ticks)
                recoverable = floor(fsum(amount for tick, amount in deficits.items() if tick >= arrival))
                limit = min(room, floor(route.max_shipment), floor(depot.inventory.get(fuel, 0)), dispatch[depot.id], recoverable)
                if limit > 0:
                    variable = model.new_int_var(0, limit, f"{forecast.id}:{route.id}")
                    choices.append(Candidate(route, forecast, arrival, limit, variable))
            for arrival in sorted({choice.arrival_tick for choice in choices}):
                late_shipments = [choice.variable for choice in choices if choice.arrival_tick >= arrival]
                usable_demand = floor(fsum(amount for tick, amount in deficits.items() if tick >= arrival))
                model.add(sum(late_shipments) <= usable_demand)
            if choices:
                model.add(sum(choice.variable for choice in choices) <= room)
                candidates.extend(choices)
                assessment.explanation = "Feasible routes competed for shared depot stock and dispatch capacity."
            else:
                assessment.explanation = "No route can deliver a whole liter against remaining unmet demand within the available capacity."

    ranked = sorted(assessments.values(), key=lambda item: (-item.priority_score, item.station_id, item.fuel_type))
    if not candidates:
        return Plan(solver_status="NO_CANDIDATES", shipments=[], assessments=ranked)

    by_depot = defaultdict(list)
    by_depot_fuel = defaultdict(list)
    for choice in candidates:
        depot_id = choice.route.source_depot_id
        by_depot[depot_id].append(choice.variable)
        by_depot_fuel[depot_id, choice.forecast.fuel_type].append(choice.variable)
    for depot_id, variables in by_depot.items():
        model.add(sum(variables) <= dispatch[depot_id])
    for (depot_id, fuel), variables in by_depot_fuel.items():
        model.add(sum(variables) <= floor(depots[depot_id].inventory[fuel]))

    model.maximize(sum(
        choice.variable * (
            25 * assessments[choice.forecast.id].priority_score + 24 - (choice.arrival_tick - snapshot.tick)
        )
        for choice in candidates
    ))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise OptimizationError("The optimizer did not return a valid plan.")

    shipments = []
    for choice in candidates:
        quantity = solver.value(choice.variable)
        if not quantity:
            continue
        assessment = assessments[choice.forecast.id]
        assessment.recommended_liters += quantity
        shipments.append(Shipment(
            prediction_id=choice.forecast.id, source_depot_id=choice.route.source_depot_id,
            destination_station_id=choice.route.destination_station_id, route_id=choice.route.id,
            fuel_type=choice.forecast.fuel_type, quantity=quantity, estimated_arrival_tick=choice.arrival_tick,
            priority_score=assessment.priority_score,
            explanation=(
                f"First projected shortage at tick {assessment.first_shortage_tick}. "
                f"This route can deliver {quantity} L by tick {choice.arrival_tick} "
                f"against a {choice.limit} L candidate limit before shared constraints."
            ),
        ))
    for item in ranked:
        if item.recommended_liters:
            item.explanation = f"The plan assigns {item.recommended_liters} L of capacity to this station and fuel."
    shipments.sort(key=lambda item: (-item.priority_score, item.estimated_arrival_tick, item.route_id, item.fuel_type))
    return Plan(solver_status=solver.status_name(status), shipments=shipments, assessments=ranked)
