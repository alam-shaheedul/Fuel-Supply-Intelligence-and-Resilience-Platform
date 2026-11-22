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

## Phase 2 — FastAPI backend foundation

With Python 3.11 or newer installed, run these commands from the repository root while the simulator is running.

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
& .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001
```

The backend exposes its own [Swagger UI](http://127.0.0.1:8001/docs) on port 8001. Verify the health endpoint from a second terminal.

```powershell
curl.exe -i http://127.0.0.1:8001/health
```

`GET /health` returns HTTP 200 with `{"status":"ok","simulator":"ok"}` when the simulator reports that its API and database are healthy. A failed simulator check returns HTTP 503 with `status` set to `degraded`, `simulator` set to `unavailable`, and a machine-readable `error.code`. The backend can start even while the simulator is offline.

Set configuration through environment variables or the root `.env` file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SIMULATOR_BASE_URL` | `http://127.0.0.1:8000` | Simulator API origin |
| `SIMULATOR_TIMEOUT_SECONDS` | `3` | HTTP client timeout in seconds |
| `SIMULATOR_STREAM_READ_TIMEOUT_SECONDS` | `30` | SSE idle read timeout in seconds. Must exceed the simulator's 15-second heartbeat interval |

## Phase 3 — Simulator integration

With the simulator and backend running, open the [backend API docs](http://127.0.0.1:8001/docs). The backend exposes these simulator routes under `/v1`.

| Method | Routes |
| --- | --- |
| GET | `/instance`, `/regions`, `/depots`, `/depots/{entity_id}`, `/stations`, `/stations/{entity_id}`, `/routes` |
| GET | `/supply-arrivals`, `/events`, `/demand-history`, `/metrics`, `/allocations` |
| POST | `/allocations`, `/allocations/{allocation_id}/cancel` |
| GET | `/stream` (server-sent events) |

```powershell
curl.exe -i "http://127.0.0.1:8001/v1/demand-history?station_id=station-mirpur&limit=5"
curl.exe -N http://127.0.0.1:8001/v1/stream
```

Demand history accepts an optional station ID and a limit from 1 to 2000 (default 200). The backend forwards `X-Simulator-Stale` on REST reads when the simulator provides it. SSE forwards the simulator's events and heartbeats. Treat events as change notifications and fetch current state over REST after a notification or reconnection. The simulator provides no event replay.

Submit an allocation with `idempotency_key`, `source_depot_id`, `destination_station_id`, `route_id`, `fuel_type`, and a positive `quantity`. Reuse the same key and body when retrying the same submission. The simulator validates inventory, capacity, and routes. Cancel by allocation ID while its status is `PENDING`.

The integration preserves simulator 404, 409, 422, and 503 statuses and surfaces the simulator's error code when available. Local request validation uses FastAPI's 422 response. An upstream timeout returns 504, an unreachable simulator returns 503, and an invalid upstream response returns 502.

## Phase 4 — PostgreSQL storage

From the repository root, start PostgreSQL and apply the schema migration.

```powershell
docker compose up -d --wait postgres
& .\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
& .\.venv\Scripts\python.exe -m alembic upgrade head
```

PostgreSQL listens on `127.0.0.1:5433` and stores data in the `postgres-data` Docker volume. To override the local development credentials on initial setup, set `POSTGRES_PASSWORD` and a matching `DATABASE_URL` in the root `.env`. The app uses a SQLAlchemy URL with the psycopg driver, such as `postgresql+psycopg://USER:PASSWORD@127.0.0.1:5433/DBNAME`. Changing the Compose password on an existing volume does not change the database role password.

Start the backend as above and call `GET /health/database` to check the PostgreSQL connection. The separate `GET /health` endpoint checks the simulator.

Import recent demand observations after the simulator has recorded observations.

```powershell
& .\.venv\Scripts\python.exe -m backend.app.db.import_history --run-id baseline-01
```

Use the same run ID for repeat imports from the same simulator run. Choose a new run ID after resetting or replacing the simulator, since source IDs may be reused. The command skips duplicates and rejects data marked stale. It imports up to 2,000 recent observations per request and supports `--limit` and `--station-id`. Run it periodically while collecting history so older observations stay within the simulator's available window.

| Table | Stored data |
| --- | --- |
| `demand_observations` | Simulator run, source observation ID, station, fuel, tick, simulation time, and demand quantities |
| `predictions` | Run, station, fuel, source and target ticks, model version, prediction kind, and result |
| `decision_logs` | Run, decision key, tick, status, explanation, optional simulator allocation ID, and details |

The functions `store_prediction` and `append_decision` in `backend.app.db.repository` accept SQLAlchemy models and write within the caller's transaction. Prediction generation and decision automation are implemented in later phases.
