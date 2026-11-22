import { useEffect, useRef, useState } from 'react'
import { readDashboard } from './api'

const updates = ['simulation.tick', 'allocation.status_changed', 'inventory.updated', 'simulator.notice']

export function useDashboard() {
  const [data, setData] = useState({})
  const [checked, setChecked] = useState(null)
  const [refreshing, setRefreshing] = useState(true)
  const [connection, setConnection] = useState('connecting')
  const refreshAction = useRef(null)

  useEffect(() => {
    let source
    let reconnect
    let scheduled
    let inFlight = false
    let queued = false
    const controller = new AbortController()

    async function refresh(visible = false) {
      if (inFlight) {
        queued = true
        return
      }
      inFlight = true
      if (visible) setRefreshing(true)
      try {
        const snapshot = await readDashboard(controller.signal)
        if (!controller.signal.aborted) {
          setData(snapshot)
          setChecked(new Date())
        }
      } finally {
        inFlight = false
        if (!controller.signal.aborted) {
          setRefreshing(false)
          if (queued) {
            queued = false
            scheduleRefresh()
          }
        }
      }
    }

    function scheduleRefresh() {
      if (scheduled != null) return
      scheduled = window.setTimeout(() => {
        scheduled = undefined
        void refresh()
      }, 1000)
    }

    function connect() {
      source = new EventSource('/api/v1/stream')
      source.onopen = () => {
        setConnection('live')
        scheduleRefresh()
      }
      source.onerror = () => {
        source.close()
        setConnection('polling')
        scheduleRefresh()
        reconnect = window.setTimeout(connect, 5000)
      }
      for (const event of updates) source.addEventListener(event, scheduleRefresh)
    }

    refreshAction.current = () => void refresh(true)
    void refresh(true)
    connect()
    const poll = window.setInterval(scheduleRefresh, 8000)
    return () => {
      refreshAction.current = null
      controller.abort()
      source?.close()
      window.clearTimeout(reconnect)
      window.clearTimeout(scheduled)
      window.clearInterval(poll)
    }
  }, [])

  return { data, checked, refreshing, connection, reload: () => refreshAction.current?.() }
}
