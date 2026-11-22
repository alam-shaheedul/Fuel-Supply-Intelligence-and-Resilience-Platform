import { useEffect, useState } from 'react'
import { request } from './api'
import { label, liters, Panel, ReadState, Status } from './overview'

const shipmentStatuses = ['PENDING', 'IN_TRANSIT', 'ARRIVED', 'FAILED', 'CANCELLED']

export function Workflow({ plan, allocations, instance, onChange }) {
  const [workflow, setWorkflow] = useState(null)
  const [operator, setOperator] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const path = `/v1/recommendations/${plan.id}`

  useEffect(() => {
    const controller = new AbortController()
    request(`${path}/workflow`, { signal: controller.signal }).then(
      ({ value }) => setWorkflow(value),
      (reason) => { if (!controller.signal.aborted) setError(reason.message) },
    )
    return () => controller.abort()
  }, [path])

  async function act(action, body) {
    setBusy(true)
    setError('')
    try {
      const { value } = await request(`${path}/${action}`, { body })
      setWorkflow(value)
      onChange()
    } catch (reason) {
      setError(reason.message)
    } finally {
      setBusy(false)
    }
  }

  const canSubmit = instance?.status === 'PAUSED' && instance.tick === plan.tick
  const byKey = new Map(allocations.map((item) => [item.idempotency_key, item]))
  return <Panel title="Operator review" action={workflow && <Status value={workflow.status} />}>
    {error && <p className="notice error" role="alert">{error}</p>}
    {!workflow && !error && <p className="empty" role="status">Loading approval status…</p>}
    {workflow?.status === 'no_shipments' && <p>No shipments in this plan require approval.</p>}
    {workflow?.status === 'proposed' && <form className="inline-form" onSubmit={(event) => {
      event.preventDefault()
      void act('approve', { operator: operator.trim() })
    }}>
      <label>Operator name<input required maxLength={80} value={operator} onChange={(event) => setOperator(event.target.value)} /></label>
      <button className="primary" disabled={busy || !canSubmit || !operator.trim()}>Approve plan</button>
    </form>}
    {workflow?.approved_by && <p className="workflow-note">Approved by <strong>{workflow.approved_by}</strong> · {new Date(workflow.approved_at).toLocaleString()}</p>}
    {workflow && ['approved', 'partial'].includes(workflow.status) &&
      <button className="primary workflow-action" disabled={busy} onClick={() => act(
        canSubmit ? 'execute' : 'execute?reconcile_only=true', {},
      )}>
        {busy ? 'Working…' : !canSubmit ? 'Check for submitted shipments' : workflow.status === 'partial' ? 'Retry remaining shipments' : 'Submit approved shipments'}
      </button>}
    {workflow && workflow.status !== 'submitted' && workflow.status !== 'no_shipments' && !canSubmit &&
      <p className="workflow-note">Approval and new submissions require the simulator to be paused at tick {plan.tick}.</p>}
    {workflow?.last_error && <p className="notice error" role="alert">{workflow.last_error}</p>}
    {workflow?.approved_by && <div className="table-scroll workflow-table"><table>
      <thead><tr><th>Route</th><th>Fuel</th><th>Quantity</th><th>Allocation</th><th>Shipment status</th></tr></thead>
      <tbody>{workflow.shipments.map((item) => {
        const current = byKey.get(item.idempotency_key)
        return <tr key={item.idempotency_key}><td>{item.proposal.route_id}</td><td>{label(item.proposal.fuel_type)}</td>
          <td>{liters(item.proposal.quantity)}</td><td>{item.allocation_id ?? '—'}</td>
          <td><Status value={current?.status ?? (item.allocation_id ? 'submitted' : 'awaiting submission')} /></td></tr>
      })}</tbody>
    </table></div>}
  </Panel>
}

export function Shipments({ entry }) {
  const [filter, setFilter] = useState('')
  return <Panel title="Simulator shipments" action={<label className="shipment-filter">Status
    <select value={filter} onChange={(event) => setFilter(event.target.value)}>
      <option value="">All statuses</option>
      {shipmentStatuses.map((item) => <option value={item} key={item}>{label(item)}</option>)}
    </select>
  </label>}>
    <ReadState entry={entry}>{(items) => {
      const visible = items.filter((item) => !filter || item.status === filter)
      return visible.length ? <div className="table-scroll"><table>
        <thead><tr><th>Allocation</th><th>Depot to station</th><th>Fuel</th><th>Quantity</th><th>Status</th><th>Expected tick</th><th>Actual tick</th></tr></thead>
        <tbody>{visible.map((item) => <tr key={item.id}>
          <td>#{item.id}</td><td>{item.source_depot_id} → {item.destination_station_id}</td>
          <td>{label(item.fuel_type)}</td><td>{liters(item.quantity)}</td><td><Status value={item.status} /></td>
          <td>{item.expected_arrival_tick ?? '—'}</td><td>{item.actual_arrival_tick ?? '—'}</td>
        </tr>)}</tbody>
      </table></div> : <p className="empty">No shipments match this view.</p>
    }}</ReadState>
  </Panel>
}
