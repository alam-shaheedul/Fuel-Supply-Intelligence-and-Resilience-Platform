import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity, AlertTriangle, Building2, ChartNoAxesCombined, ChevronRight, ClipboardList,
  Database, Fuel, History as HistoryIcon, LayoutDashboard, RefreshCw, Server, Truck,
} from 'lucide-react'
import { readDashboard } from './api'
import { format, Inventory, Overview, Panel, Status } from './overview'
import { History, Predictions, Recommendations } from './planning'
import './style.css'

const pages = [
  { id: 'overview', name: 'Overview', title: 'Operations overview', subtitle: 'Monitor fuel availability and supply performance across the network.', icon: LayoutDashboard },
  { id: 'depots', name: 'Depots', title: 'Depot management', subtitle: 'Review inventory, tank capacity, and dispatch limits by depot.', icon: Building2 },
  { id: 'stations', name: 'Stations', title: 'Station monitoring', subtitle: 'Check fuel levels and operating status across the station network.', icon: Fuel },
  { id: 'predictions', name: 'Predictions & alerts', title: 'Predictions & alerts', subtitle: 'Forecast fuel demand and identify potential shortages and anomalies.', icon: ChartNoAxesCombined },
  { id: 'recommendations', name: 'Recommendations', title: 'Allocation recommendations', subtitle: 'Generate a proposed shipment plan from saved forecasts.', icon: Truck },
  { id: 'history', name: 'Decision history', title: 'Decision history', subtitle: 'Review saved plans and their station-level explanations.', icon: HistoryIcon },
  { id: 'health', name: 'System health', title: 'System health', subtitle: 'Check the backend, database, and simulator connection.', icon: Activity },
]

function currentPage() {
  return pages.find((item) => item.id === window.location.hash.slice(1)) ?? pages[0]
}

function Health({ data }) {
  const services = [
    { title: 'Backend', icon: Server, entry: data.backend, available: Boolean(data.backend?.value || data.backend?.status === 503) },
    { title: 'PostgreSQL', icon: Database, entry: data.database, available: data.database?.value?.database === 'ok' },
    { title: 'Simulator', icon: Activity, entry: data.instance, available: Boolean(data.instance?.value) },
  ]
  return <div className="inventory-grid">{services.map(({ title, icon: Icon, entry, available }) => <Panel
    key={title} title={title}
    action={<Status value={!entry ? 'checking' : available ? 'reachable' : 'unavailable'} tone={!entry ? 'neutral' : available ? 'good' : 'bad'} />}>
    <p className="service-info"><Icon size={21} aria-hidden="true" />{entry?.error ?? (entry ? 'Responding to requests' : 'Checking connection…')}</p>
  </Panel>)}</div>
}

function App() {
  const [page, setPage] = useState(currentPage)
  const [data, setData] = useState({})
  const [checked, setChecked] = useState(null)
  const [refresh, setRefresh] = useState(0)
  const [refreshing, setRefreshing] = useState(true)
  const [predictions, setPredictions] = useState([])
  const [recommendation, setRecommendation] = useState(null)

  useEffect(() => {
    const update = () => setPage(currentPage())
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    readDashboard(controller.signal).then((value) => {
      if (controller.signal.aborted) return
      setData(value)
      setChecked(new Date())
      setRefreshing(false)
    })
    return () => controller.abort()
  }, [refresh])

  function navigate(id) {
    window.location.hash = id
  }

  function reload() {
    setRefreshing(true)
    setRefresh((value) => value + 1)
  }

  function savePrediction(value) {
    setPredictions((items) => [value, ...items.filter((item) => (
      item.run_id !== value.run_id || item.station_id !== value.station_id || item.fuel_type !== value.fuel_type
    ))].slice(0, 24))
  }

  function saveRecommendation(value) {
    setRecommendation(value)
    reload()
  }

  const instance = data.instance?.value
  const stale = Object.values(data).some((entry) => entry.stale)
  const views = {
    overview: <Overview data={data} navigate={navigate} />,
    depots: <Inventory entry={data.depots} kind="depots" />,
    stations: <Inventory entry={data.stations} kind="stations" />,
    predictions: <Predictions stations={data.stations?.value ?? []} prediction={predictions[0]} onPrediction={savePrediction} />,
    recommendations: <Recommendations predictions={predictions} tick={instance?.tick} value={recommendation} onRecommendation={saveRecommendation} />,
    history: <History entry={data.history} />,
    health: <Health data={data} />,
  }

  return <div className="layout">
    <a className="skip-link" href="#content" onClick={(event) => { event.preventDefault(); document.getElementById('content').focus() }}>Skip to content</a>
    <aside className="sidebar">
      <a href="#overview" className="brand">
        <span className="brand-mark"><Fuel size={22} aria-hidden="true" /></span>
        <span><strong>Fuel Supply</strong><small>Intelligence Platform</small></span>
      </a>
      <nav aria-label="Dashboard">
        {pages.map(({ id, name, icon: Icon }) => <a key={id} href={`#${id}`} aria-current={page.id === id ? 'page' : undefined}>
          <Icon size={18} aria-hidden="true" /><span>{name}</span>
        </a>)}
      </nav>
      <div className="sidebar-bottom"><div><Activity size={17} aria-hidden="true" />Simulator</div>
        <Status value={instance?.status ?? (data.instance ? 'unavailable' : 'checking')} tone={data.instance?.error ? 'bad' : undefined} />
      </div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="breadcrumb"><ClipboardList size={17} aria-hidden="true" /><span>Operations</span><ChevronRight size={15} aria-hidden="true" /><strong>{page.name}</strong></div>
        <div className="top-status"><span className="tick">Tick <strong>{format(instance?.tick)}</strong></span>
          <button className="secondary" onClick={reload} disabled={refreshing}><RefreshCw size={16} aria-hidden="true" />{refreshing ? 'Refreshing…' : 'Refresh'}</button></div>
      </header>
      <main id="content" tabIndex={-1}>
        <div className="page-title"><div><h1>{page.title}</h1><p>{page.subtitle}</p></div>
          {checked && <span className="last-checked">Checked {checked.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>}
        </div>
        {stale && <p role="alert" className="notice"><AlertTriangle size={18} aria-hidden="true" />The simulator marked some dashboard data as stale.</p>}
        {data.instance?.error && page.id !== 'health' && <p role="alert" className="notice error"><AlertTriangle size={18} aria-hidden="true" />Simulator connection unavailable.</p>}
        {views[page.id]}
      </main>
    </div>
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
