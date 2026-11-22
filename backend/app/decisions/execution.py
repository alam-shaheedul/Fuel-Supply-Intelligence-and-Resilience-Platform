from collections import defaultdict

from .models import Shipment, Snapshot


def active_reservations(snapshot: Snapshot) -> tuple[defaultdict[str, float], defaultdict[tuple[str, str], float]]:
    dispatch = defaultdict(float)
    inbound = defaultdict(float)
    for item in snapshot.allocations:
        if item.status in {"PENDING", "IN_TRANSIT"}:
            inbound[item.destination_station_id, item.fuel_type] += item.quantity
            if item.status == "PENDING" or snapshot.tick in (item.created_tick, item.departure_tick):
                dispatch[item.source_depot_id] += item.quantity
    return dispatch, inbound


def capacity_issue(snapshot: Snapshot, shipments: list[Shipment]) -> str | None:
    depots = {item.id: item for item in snapshot.depots}
    stations = {item.id: item for item in snapshot.stations}
    routes = {item.id: item for item in snapshot.routes}
    dispatch, inbound = active_reservations(snapshot)
    stock = defaultdict(float)

    for shipment in shipments:
        depot_id, station_id, fuel = shipment.source_depot_id, shipment.destination_station_id, shipment.fuel_type
        route = routes.get(shipment.route_id)
        depot = depots.get(depot_id)
        station = stations.get(station_id)
        amount = shipment.quantity
        if (
            route is None or route.status != "AVAILABLE" or route.source_depot_id != depot_id
            or route.destination_station_id != station_id or depot is None
            or depot.status not in {"OPEN", "CONSTRAINED"} or station is None or station.status != "OPEN"
        ):
            return f"Route {shipment.route_id} or its depot or station is unavailable."
        if amount <= 0 or amount > route.max_shipment:
            return f"Shipment on {route.id} exceeds its current route limit."
        dispatch[depot_id] += amount
        stock[depot_id, fuel] += amount
        inbound[station_id, fuel] += amount
        if dispatch[depot_id] > depot.dispatch_capacity_per_tick:
            return f"Depot {depot_id} has insufficient dispatch capacity."
        if stock[depot_id, fuel] > depot.inventory.get(fuel, 0):
            return f"Depot {depot_id} has insufficient {fuel} stock."
        if station.inventory.get(fuel, 0) + inbound[station_id, fuel] > station.capacity.get(fuel, 0):
            return f"Station {station_id} has insufficient {fuel} tank space."
    return None
