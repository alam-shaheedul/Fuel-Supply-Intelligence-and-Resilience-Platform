import { useState } from 'react'
import { AlertTriangle, ChartNoAxesCombined, ClipboardList, Truck } from 'lucide-react'
import { request } from './api'
import { format, fuels, label, liters, Panel, ReadState, Status } from './overview'
import { Workflow } from './workflow'

export function Predictions({ stations, prediction, onPrediction }) {
  const [run, setRun] = useState('baseline-01')
  const [station, setStation] = useState('')
  const [fuel, setFuel] = useState('DIESEL')
  const [horizon, setHorizon] = useState(8)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const selected = stations.some((item) => item.id === station) ? station : stations[0]?.id ?? ''
  const result = prediction?.result
  const points = result?.demand_forecast ?? []
  const max = Math.max(1, ...points.map((point) => point.liters))

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { value } = await request('/v1/predictions', {
        body: { run_id: run.trim(), station_id: selected, fuel_type: fuel, horizon_ticks: Number(horizon) },
      })
      onPrediction(value)
    } catch (reason) {
      setError(reason.message)
    } finally {
      setBusy(false)
    }
  }

  return <>
    <Panel title="Generate a forecast" caption="Use the run ID from the latest demand-history import.">
      <form className="field-grid" onSubmit={submit}>
        <label>Run ID<input required maxLength={100} value={run} onChange={(event) => setRun(event.target.value)} /></label>
        <label>Station<select required value={selected} onChange={(event) => setStation(event.target.value)}>
          {!stations.length && <option value="">No stations available</option>}
          {stations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <label>Fuel<select value={fuel} onChange={(event) => setFuel(event.target.value)}>
          {fuels.map((item) => <option key={item} value={item}>{label(item)}</option>)}
        </select></label>
        <label>Horizon (ticks)<input type="number" min={1} max={24} required value={horizon} onChange={(event) => setHorizon(event.target.value)} /></label>
        <button className="primary" disabled={busy || !selected || !run.trim()}>{busy ? 'Generating…' : 'Generate forecast'}</button>
      </form>
      {error && <p role="alert" className="notice error">{error}</p>}
    </Panel>
    {!result ? <Panel title="Demand forecast"><p className="empty"><ChartNoAxesCombined aria-hidden="true" />Generate a forecast to see demand and risk estimates.</p></Panel> : <>
      <Panel title="Demand forecast" caption={`Prediction #${prediction.id} · ${prediction.station_id} · ${label(prediction.fuel_type)} · ${prediction.run_id}`}
        action={<Status value={result.forecast_method} />}>
        <ol className="forecast-chart" aria-label="Forecast demand by simulator tick">
          {points.map((point) => <li key={point.tick} title={`Tick ${point.tick}: ${liters(point.liters)}`}>
            <span>{format(point.liters)}</span>
            <div className="chart-column" aria-hidden="true"><i style={{ height: `${100 * point.liters / max}%` }} /></div>
            <small>{point.tick}</small>
          </li>)}
        </ol>
        <p className="panel-foot">Demand in liters by simulator tick</p>
      </Panel>
      <div className="overview-grid">
        <Panel title="Shortage outlook" caption="Assumes no replenishment during the forecast">
          <dl className="details"><dt>First shortage</dt><dd>{result.shortage.first_shortage_tick ?? 'None projected'}</dd>
            <dt>Projected unmet demand</dt><dd>{liters(result.shortage.projected_unmet_liters)}</dd>
            <dt>Projected ending inventory</dt><dd>{liters(result.shortage.projected_ending_inventory_liters)}</dd></dl>
        </Panel>
        <Panel title="Demand anomaly" action={<Status value={result.anomaly.status} />}>
          <dl className="details"><dt>Observed demand</dt><dd>{liters(result.demand_summary.latest_liters)}</dd>
            <dt>Recent average</dt><dd>{liters(result.demand_summary.mean_liters)}</dd>
            <dt>Forecast MAE</dt><dd>{result.evaluation ? liters(result.evaluation.mae_liters) : 'Insufficient history'}</dd></dl>
        </Panel>
      </div>
    </>}
  </>
}

export function RecommendationDetail({ value }) {
  const { plan } = value
  return <>
    <Panel title="Proposed shipments" caption={`Plan #${value.id} · ${value.run_id} · Tick ${value.tick}`} action={<Status value={plan.solver_status} />}>
      {plan.shipments.length ? <div className="shipments">{plan.shipments.map((item) => <article key={`${item.route_id}-${item.fuel_type}`}>
        <div><Truck size={18} aria-hidden="true" /><strong>{liters(item.quantity)} {label(item.fuel_type)}</strong>
          <span>Arrival tick {item.estimated_arrival_tick}</span></div>
        <p>{item.source_depot_id} → {item.destination_station_id} <small>· {item.route_id}</small></p>
        <p>{item.explanation}</p>
      </article>)}</div> : <p className="empty">No shipments were recommended for this plan.</p>}
    </Panel>
    <Panel title="Station assessments">
      <div className="table-scroll"><table><thead><tr><th>Station and fuel</th><th>Unmet demand</th><th>Priority</th><th>Recommended</th><th>Explanation</th></tr></thead>
        <tbody>{plan.assessments.map((item) => <tr key={item.prediction_id}><td>{item.station_id} · {label(item.fuel_type)}</td>
          <td>{liters(item.projected_unmet_liters)}</td><td>{item.priority_score}</td><td>{liters(item.recommended_liters)}</td><td>{item.explanation}</td></tr>)}</tbody>
      </table></div>
    </Panel>
  </>
}

export function Recommendations({ predictions, instance, allocations, value, onRecommendation, onChange }) {
  const [manual, setManual] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const candidateRun = predictions.find((item) => item.source_tick + 1 === instance?.tick)?.run_id
  const current = predictions.filter((item) => item.source_tick + 1 === instance?.tick && item.run_id === candidateRun)
  const suggested = current.map((item) => item.id).join(', ')
  const input = manual ?? suggested

  async function submit(event) {
    event.preventDefault()
    const ids = input.trim().split(/[\s,]+/).map(Number)
    if (!ids.every((id) => Number.isSafeInteger(id) && id > 0 && id <= 2147483647)
      || ids.length > 24 || new Set(ids).size !== ids.length) {
      setError('Enter 1–24 distinct positive prediction IDs.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const { value: result } = await request('/v1/recommendations', { body: { prediction_ids: ids } })
      onRecommendation(result)
    } catch (reason) {
      setError(reason.message)
    } finally {
      setBusy(false)
    }
  }

  return <>
    <Panel title="Generate a recommendation" caption="Choose saved predictions from the same run and simulator tick.">
      <form className="inline-form" onSubmit={submit}>
        <label>Prediction IDs<input required placeholder="Comma-separated IDs" value={input} onChange={(event) => setManual(event.target.value)} /></label>
        <button className="primary" disabled={busy || !input.trim()}>{busy ? 'Generating…' : 'Generate plan'}</button>
      </form>
      {manual !== null && suggested && <button className="text-button reset" onClick={() => setManual(null)}>Use current forecasts</button>}
      {error && <p className="notice error" role="alert">{error}</p>}
    </Panel>
    {value ? <>
      <RecommendationDetail value={value} />
      <Workflow key={value.id} plan={value} instance={instance} allocations={allocations} onChange={onChange} />
    </> : <Panel title="Proposed shipments"><p className="empty"><ClipboardList aria-hidden="true" />Generate a plan to review allocation recommendations.</p></Panel>}
  </>
}

export function History({ entry, instance, allocations, onChange }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(null)
  const [error, setError] = useState('')

  async function open(id) {
    setLoading(id)
    setError('')
    try {
      const { value } = await request(`/v1/recommendations/${id}`)
      setDetail(value)
    } catch (reason) {
      setError(reason.message)
    } finally {
      setLoading(null)
    }
  }

  return <>
    <Panel title="Saved plans" caption="The 50 most recent recommendations">
      <ReadState entry={entry}>{(items) => items.length ? <div className="table-scroll"><table>
        <thead><tr><th>Plan</th><th>Run</th><th>Tick</th><th>Shipments</th><th>Solver status</th><th><span className="sr-only">Action</span></th></tr></thead>
        <tbody>{items.map((item) => <tr key={item.id}><td>#{item.id}</td><td>{item.run_id}</td><td>{item.tick}</td><td>{item.shipment_count}</td>
          <td><Status value={item.solver_status} /></td><td><button className="text-button" disabled={loading !== null} onClick={() => open(item.id)} aria-label={`View plan ${item.id}`}>{loading === item.id ? 'Opening…' : 'View plan'}</button></td></tr>)}</tbody>
      </table></div> : <p className="empty">No saved recommendations.</p>}</ReadState>
      {error && <p className="notice error" role="alert"><AlertTriangle size={18} aria-hidden="true" />{error}</p>}
    </Panel>
    {detail && <>
      <RecommendationDetail value={detail} />
      <Workflow key={detail.id} plan={detail} instance={instance} allocations={allocations} onChange={onChange} />
    </>}
  </>
}
