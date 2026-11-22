export async function request(path, { body, signal } = {}) {
  const response = await fetch(`/api${path}`, {
    signal,
    ...(body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok || payload === null) {
    const detail = payload?.detail ?? payload?.error
    const error = new Error(
      detail?.message ?? (typeof detail === 'string' ? detail : `Request failed (HTTP ${response.status}).`),
    )
    error.status = response.status
    throw error
  }
  return { value: payload, stale: response.headers.get('X-Simulator-Stale')?.toLowerCase() === 'true' }
}

const reads = {
  backend: '/health',
  database: '/health/database',
  instance: '/v1/instance',
  depots: '/v1/depots',
  stations: '/v1/stations',
  routes: '/v1/routes',
  metrics: '/v1/metrics',
  allocations: '/v1/allocations',
  arrivals: '/v1/supply-arrivals',
  history: '/v1/recommendations?limit=50',
}

export async function readDashboard(signal) {
  const entries = await Promise.all(Object.entries(reads).map(async ([key, path]) => {
    try {
      return [key, await request(path, { signal })]
    } catch (error) {
      return [key, { error: error.message, status: error.status }]
    }
  }))
  return Object.fromEntries(entries)
}

const cacheable = new Set(['instance', 'depots', 'stations', 'routes', 'metrics', 'allocations', 'arrivals'])

export function mergeDashboard(previous, next) {
  return Object.fromEntries(Object.entries(next).map(([key, entry]) => {
    if (!cacheable.has(key) || (!entry.error && !entry.stale)) return [key, entry]
    const prior = previous[key]
    if (prior?.value !== undefined && (prior.cached || (!prior.error && !prior.stale))) {
      return [key, { ...entry, value: prior.value, cached: true }]
    }
    return [key, entry]
  }))
}
