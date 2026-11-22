import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mergeDashboard } from '../src/api.js'
import { subscribeSimulator } from '../src/live.js'

test('outages and stale responses preserve only last known fresh simulator data', () => {
  const original = { instance: { value: { tick: 12 } }, backend: { value: 'ok' } }
  const degraded = mergeDashboard(original, {
    instance: { value: { tick: 13 }, stale: true },
    backend: { error: 'Unavailable' },
  })
  assert.deepEqual(degraded.instance, { value: { tick: 12 }, stale: true, cached: true })
  assert.deepEqual(degraded.backend, { error: 'Unavailable' })
  const offline = mergeDashboard(degraded, { instance: { error: 'Unavailable' } })
  assert.deepEqual(offline.instance, { value: { tick: 12 }, cached: true, error: 'Unavailable' })
  const recovered = mergeDashboard(offline, { instance: { value: { tick: 16 } } })
  assert.deepEqual(recovered.instance, { value: { tick: 16 } })
})

test('a cold start cannot promote a stale snapshot to the last known fresh value', () => {
  const stale = mergeDashboard({}, { stations: { value: [], stale: true } })
  const unavailable = mergeDashboard(stale, { stations: { error: 'Offline' } })
  assert.deepEqual(unavailable.stations, { error: 'Offline' })
})

test('SSE falls back to polling signal, backs off, and refreshes after reconnecting', (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const streams = []
  class FakeStream {
    constructor(url) {
      assert.equal(url, '/api/v1/stream')
      this.events = new Map()
      streams.push(this)
    }
    addEventListener(name, handle) { this.events.set(name, handle) }
    close() { this.closed = true }
  }
  let refreshes = 0
  const modes = []
  const unsubscribe = subscribeSimulator(() => refreshes++, (mode) => modes.push(mode), FakeStream)
  streams[0].onerror()
  assert.equal(streams[0].closed, true)
  assert.deepEqual(modes, ['polling'])
  context.mock.timers.tick(5000)
  streams[1].onerror()
  context.mock.timers.tick(9999)
  assert.equal(streams.length, 2)
  context.mock.timers.tick(1)
  streams[2].onopen()
  streams[2].events.get('allocation.status_changed')()
  assert.deepEqual(modes, ['polling', 'polling', 'live'])
  assert.equal(refreshes, 4)
  streams[2].onerror()
  context.mock.timers.tick(5000)
  assert.equal(streams.length, 4)
  unsubscribe()
  assert.equal(streams[3].closed, true)
})
