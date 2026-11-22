import { Activity, Building2, Droplets, Fuel, Gauge, Route } from 'lucide-react'

export const fuels = ['DIESEL', 'PETROL', 'OCTANE']
export const number = new Intl.NumberFormat('en', { maximumFractionDigits: 1 })
export const format = (value) => Number.isFinite(value) ? number.format(value) : '—'
export const liters = (value) => Number.isFinite(value) ? `${format(value)} L` : '—'
export const label = (value) => value?.toLowerCase().replaceAll('_', ' ') ?? 'unknown'

export function Status({ value, tone }) {
  const color = tone ?? ({
    OPEN: 'good', AVAILABLE: 'good', RUNNING: 'good', ok: 'good',
    PAUSED: 'warn', CONSTRAINED: 'warn', anomaly: 'warn', DISRUPTED: 'bad',
  }[value] ?? 'neutral')
  return <span className={`status ${color}`}>{label(value)}</span>
}

export function Panel({ title, caption, action, children, className = '' }) {
  return <section className={`panel ${className}`}>
    <div className="panel-head"><div><h2>{title}</h2>{caption && <p>{caption}</p>}</div>{action}</div>
    {children}
  </section>
}

export function ReadState({ entry, children }) {
  if (!entry) return <p className="empty" role="status">Loading data…</p>
  if (entry.error) return <p className="notice error" role="alert">{entry.error}</p>
  return children(entry.value)
}

export function InventoryMeter({ stock, capacity, fuel }) {
  const percentage = capacity > 0 ? Math.min(100, Math.max(0, stock / capacity * 100)) : 0
  return <div className="meter-row">
    <div className="meter-label"><span><i className={`fuel-dot ${fuel.toLowerCase()}`} />{label(fuel)}</span>
      <strong>{liters(stock)} <small>/ {liters(capacity)}</small></strong></div>
    <div className="meter" aria-hidden="true"><div className={fuel.toLowerCase()} style={{ width: `${percentage}%` }} /></div>
  </div>
}

function Stat({ icon: Icon, caption, value, detail, tone = '' }) {
  return <section className="stat">
    <div className={`stat-icon ${tone}`}><Icon size={19} aria-hidden="true" /></div>
    <div className="stat-caption">{caption}</div>
    <strong>{value}</strong><small>{detail}</small>
  </section>
}

const totalStock = (items) => items?.reduce((sum, item) => sum + Object.values(item.inventory).reduce((a, b) => a + b, 0), 0)

export function Overview({ data, navigate }) {
  const depots = data.depots?.value
  const stations = data.stations?.value
  const routes = data.routes?.value
  const metrics = data.metrics?.value
  const service = metrics?.service_level
  return <>
    <div className="stat-grid">
      <Stat icon={Droplets} caption="Depot inventory" value={liters(totalStock(depots))} detail={depots ? `Across ${depots.length} depots` : 'Depot data unavailable'} />
      <Stat icon={Fuel} caption="Stations open" value={stations ? `${stations.filter((item) => item.status === 'OPEN').length} / ${stations.length}` : '—'} detail={liters(totalStock(stations)) + ' in station tanks'} tone="green" />
      <Stat icon={Route} caption="Routes available" value={routes ? `${routes.filter((item) => item.status === 'AVAILABLE').length} / ${routes.length}` : '—'} detail={routes ? `${routes.filter((item) => item.status === 'DISRUPTED').length} disrupted` : 'Route data unavailable'} tone="blue" />
      <Stat icon={Gauge} caption="Service level" value={Number.isFinite(service) ? `${format(service * 100)}%` : '—'} detail={metrics ? `${liters(metrics.unmet_demand_liters)} unmet demand` : 'Metrics unavailable'} tone="amber" />
    </div>
    <div className="overview-grid">
      <Panel title="Depot inventory" caption="Current stock against tank capacity" action={<button className="text-button" onClick={() => navigate('depots')}>View depots</button>}>
        <ReadState entry={data.depots}>{(items) => items.length ? items.map((depot) => <div className="depot-overview" key={depot.id}>
          <div className="depot-title"><strong><Building2 size={16} aria-hidden="true" />{depot.name}</strong><Status value={depot.status} /></div>
          {fuels.map((fuel) => <InventoryMeter key={fuel} fuel={fuel} stock={depot.inventory[fuel]} capacity={depot.capacity[fuel]} />)}
        </div>) : <p className="empty">No depots returned.</p>}</ReadState>
      </Panel>
      <Panel title="Station monitoring" caption="Fuel on hand across station tanks" action={<button className="text-button" onClick={() => navigate('stations')}>View stations</button>}>
        <ReadState entry={data.stations}>{(items) => items.length ? items.map((station) => <div className="station-row" key={station.id}>
          <span className="station-icon"><Fuel size={18} aria-hidden="true" /></span>
          <div><strong>{station.name}</strong><small>{liters(totalStock([station]))} on hand</small></div>
          <Status value={station.status} />
        </div>) : <p className="empty">No stations returned.</p>}</ReadState>
      </Panel>
    </div>
    <Panel title="Supply performance" caption="Cumulative metrics from the simulator">
      <ReadState entry={data.metrics}>{(value) => <div className="metric-list">
        <div><Activity size={18} aria-hidden="true" /><span>Demand served</span><strong>{liters(value.served_demand_liters)}</strong></div>
        <div><Droplets size={18} aria-hidden="true" /><span>Demand unmet</span><strong>{liters(value.unmet_demand_liters)}</strong></div>
        <div><Route size={18} aria-hidden="true" /><span>Fuel allocated</span><strong>{liters(value.allocation_liters)}</strong></div>
      </div>}</ReadState>
    </Panel>
  </>
}

export function Inventory({ entry, kind }) {
  return <ReadState entry={entry}>{(items) => items.length ? <div className="inventory-grid">
    {items.map((item) => <Panel key={item.id} title={item.name} caption={item.id} action={<Status value={item.status} />}>
      {fuels.map((fuel) => <InventoryMeter key={fuel} fuel={fuel} stock={item.inventory[fuel]} capacity={item.capacity[fuel]} />)}
      <p className="panel-foot">{kind === 'depots' ? `Dispatch capacity ${liters(item.dispatch_capacity_per_tick)} per tick` : `Demand multiplier ${format(item.demand_multiplier)}×`}</p>
    </Panel>)}
  </div> : <p className="empty">No records returned.</p>}</ReadState>
}
