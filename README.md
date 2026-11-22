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

The functions `store_prediction` and `append_decision` in `backend.app.db.repository` accept SQLAlchemy models and write within the caller's transaction.

## Phase 5 — Demand intelligence

Install the updated backend requirements, start the simulator and PostgreSQL, and apply the database migration. For a consistent example, pause the simulator, import its latest demand history, and use the same run ID in the prediction request. A paused simulator must have processed at least one tick to have demand observations.

```powershell
& .\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -m backend.app.db.import_history --run-id baseline-01
```

With the backend running on port 8001, generate and retrieve a prediction.

```powershell
$body = @{
    run_id = "baseline-01"
    station_id = "station-mirpur"
    fuel_type = "DIESEL"
    horizon_ticks = 8
} | ConvertTo-Json
$prediction = Invoke-RestMethod http://127.0.0.1:8001/v1/predictions -Method Post -ContentType application/json -Body $body
$prediction | ConvertTo-Json -Depth 8
Invoke-RestMethod "http://127.0.0.1:8001/v1/predictions/$($prediction.id)"
```

The API uses up to 512 consecutive stored ticks for the requested station and fuel. A scikit-learn ridge model uses recent demand lags and a rolling mean. With fewer than 36 consecutive observations, the forecast uses the last observed demand. The result includes descriptive statistics, per-tick demand for a horizon of 1–24 ticks, a shortage projection, the latest anomaly check, and forecast evaluation when sufficient history exists. Each generated result is saved to PostgreSQL.

- Shortage projections subtract forecast demand from current inventory and assume zero replenishment during the horizon.
- Anomaly detection compares the latest observation with the previous 12–48 observations using a robust median and median absolute deviation score. With less history, it reports insufficient history.
- Evaluation uses a chronological holdout with expanding forecast origins. It reports mean absolute error (MAE), root mean squared error (RMSE), and the MAE of a last-observation baseline. Evaluation is unavailable when the holdout is smaller than eight observations or the requested horizon.
- The API rejects stale inventory. If the simulator moves while inventory is being read or the stored demand ends before the last completed tick, retry with a stable snapshot and a current import from the same simulator run.

For the Phase 5 verification, the baseline simulator ran with seed `12345` for 140 ticks at 15 simulated minutes per tick. It produced 1,680 observations across 12 station-and-fuel series. At an eight-tick forecast horizon, the final 28 observations per series were held out. Across those series, mean MAE was **22.23 L** for ridge and **22.83 L** for the last-observation baseline. Mean ridge RMSE was **31.79 L**. These figures describe this one baseline simulation run.

## Phase 6 — Allocation recommendations

Install the updated backend requirements to add Google OR-Tools. Generate predictions at the same simulator tick for the station and fuel combinations you want to plan together. Submit their saved IDs as one request to let the solver account for shared depot stock and dispatch capacity.

```powershell
$body = @{ prediction_ids = @($prediction.id) } | ConvertTo-Json
$recommendation = Invoke-RestMethod http://127.0.0.1:8001/v1/recommendations -Method Post -ContentType application/json -Body $body
$recommendation | ConvertTo-Json -Depth 8
Invoke-RestMethod "http://127.0.0.1:8001/v1/recommendations/$($recommendation.id)"
```

- The backend checks current depot and station inventory, routes, and active allocations. Predictions must come from one run and start at the current simulator tick. Use a common forecast horizon when comparing stations.
- Each station and fuel gets a score based on when its first shortage is projected and what share of forecast demand is projected to go unmet after scheduled arrivals. Earlier and larger shortages get more weight. The solver maximizes weighted shortage coverage and favors faster routes when priority is equal.
- The solver recommends whole liters and enforces route shipment limits, depot stock, shared dispatch capacity, and station tank space. Pending and in-transit shipments reserve tank space. Pending shipments and current-tick in-transit shipments reserve dispatch capacity. Estimated arrival is the current tick plus route transit time. A shipment can only cover forecast shortages at or after this tick.
- The response explains each selected shipment and includes an assessment for every requested station and fuel, including those that received no recommendation. Each plan is saved to the decision log and can be retrieved by ID.

The recommendation endpoint reads simulator state and saves a proposed plan in PostgreSQL. The operator should recheck capacity against current simulator state before submitting an allocation through `POST /v1/allocations`.

## Phase 7 — React dashboard

Start the simulator and PostgreSQL with Docker Compose, apply the database migration, and run FastAPI on port 8001 using the commands above. In a second terminal, install the locked frontend dependencies and start Vite. Use Node.js 22.12 or newer.

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The overview shows simulator inventory and service metrics. The depot and station views show stock by fuel, and System Health checks the simulator, backend, and PostgreSQL. Use **Refresh** to reload the dashboard snapshot.

To generate a forecast, pause the simulator and import demand history as described above. Enter the same run ID in **Predictions & alerts**. **Recommendations** can use prediction IDs generated during the current dashboard session or IDs you enter. **Decision history** lists the 50 most recent saved plans and opens their details. The dashboard generates proposals for review.

The development proxy forwards `/api` to `http://127.0.0.1:8001`. Set `FUEL_BACKEND_URL` in the Vite process environment if your backend runs at a different address. Run `npm.cmd --prefix frontend run build` to create a static bundle in `frontend/dist`. A server hosting that bundle must route same-origin `/api/*` requests to FastAPI with the `/api` prefix removed.

## Phase 8 — Live operations and operator approval

Start the dashboard, FastAPI, simulator, and PostgreSQL as above. The dashboard refreshes snapshots from the backend when simulator events arrive over SSE. It also polls every eight seconds and reconnects if the stream is interrupted. The header shows whether the stream is live or the dashboard is using polling.

1. Pause the simulator, import current demand history, and generate forecasts for the affected stations and fuels.
2. In **Recommendations**, generate a plan from those forecasts. Review the proposed quantities, routes, and station assessments.
3. Enter an operator name and select **Approve plan**. The name is recorded in the audit trail as entered. This local workflow assumes trusted operators and does not authenticate that name.
4. Select **Submit approved shipments** while the simulator is still paused at the original plan tick. The backend rechecks route availability, stock, dispatch capacity, and tank space against current simulator data before submitting.
5. Use **Shipments** or reopen the plan in **Decision history** to monitor allocation status and arrival ticks. If a submission is interrupted, retry from the saved plan. The backend uses stable idempotency keys to reconcile prior submissions and avoid duplicates. New shipments require a new plan after the simulator advances or is reset.

The recommendation workflow uses `GET /v1/recommendations/{id}/workflow`, `POST /v1/recommendations/{id}/approve` with `{"operator":"Name"}`, and `POST /v1/recommendations/{id}/execute`. Add `?reconcile_only=true` to check previously accepted allocations without submitting new ones. Approval records, allocation references, and the latest execution error are stored with the existing PostgreSQL decision log. There is no new migration for this workflow.

## Phase 9 — Crisis and recovery tests

With Docker Desktop running and the backend dependencies installed, run these commands from the repository root.

```powershell
& .\.venv\Scripts\python.exe -m scripts.test_crisis
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

The crisis harness starts a disposable paused simulator on a random loopback port, runs FastAPI against it, and stops it after the test. It exercises demand spikes, route disruptions, constrained depots, delayed depot resupply, API unavailability, stale responses, and SSE disconnects. It verifies that the optimizer avoids disrupted routes, checks depot capacity, and that delayed deliveries are later marked arrived. The shipment-delay event affects scheduled depot resupply, which appears under **Shipments → Depot resupply**.

During an outage or stale response, the dashboard labels previously fresh values as cached. The approval view requires fresh logistics data before enabling new submissions, and the backend rechecks the simulator state. REST polling continues during a stream interruption. SSE reconnect attempts start at five seconds, back off to 30 seconds, and reset after a successful connection. Reconnection triggers a fresh REST read.
