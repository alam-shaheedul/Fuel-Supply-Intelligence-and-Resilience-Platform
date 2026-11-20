# Fuel Supply Intelligence and Resilience Platform

## Phase 1 — Environment and simulator setup

Open Docker Desktop with the Linux containers engine running. From this folder, run the following PowerShell commands.

```powershell
docker version
docker compose version
docker compose config
docker compose pull
docker compose up -d
docker compose ps
```

The Compose file pins the organizers' simulator image to `1.0.0` and exposes it locally on port 8000. The simulator starts paused. The clock defaults to 15 simulated minutes per tick and 8 ticks per wall-clock second when running. These values follow the guide's quick-reference table. Override `TICK_MINUTES`, `SIMULATION_SPEED`, or `SIMULATOR_START_MODE` through a local `.env` file when needed.

After the container starts, run the read-only smoke test.

```powershell
& .\scripts\Test-Simulator.ps1
```

The script verifies simulator and database health, the current instance, the baseline counts of two regions, two depots, four stations, and six routes, and the HTTP responses for Swagger UI and the admin dashboard.

- In [Swagger UI](http://localhost:8000/docs), expand `GET /v1/health`, select **Try it out**, and execute the request. Expect HTTP 200 with simulator and database status `ok`. Repeat with `GET /v1/instance` to inspect the clock and current state.
- In the [admin dashboard](http://localhost:8000/admin), inspect the instance state, metrics, row counts, and recent audit entries. On a fresh paused instance, use **Step one tick**. Confirm the instance advances from tick 0 to tick 1 and the audit log records `simulation.tick` for the processed tick (tick 0). This changes local simulator state.

If a request fails during startup, inspect the logs and rerun the smoke test once the API is ready.

```powershell
docker compose logs --tail 100 simulator-api
```

Use `docker compose stop` to stop the simulator and `docker compose start` to resume the existing container.
