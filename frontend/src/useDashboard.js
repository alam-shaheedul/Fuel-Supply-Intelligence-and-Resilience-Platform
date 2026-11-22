import { useEffect, useRef, useState } from 'react'
import { mergeDashboard, readDashboard } from './api'
import { subscribeSimulator } from './live'

export function useDashboard() {
  const [data, setData] = useState({})
  const [checked, setChecked] = useState(null)
  const [refreshing, setRefreshing] = useState(true)
  const [connection, setConnection] = useState('connecting')
  const refreshAction = useRef(null)

  useEffect(() => {
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
          setData((previous) => mergeDashboard(previous, snapshot))
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

    refreshAction.current = () => void refresh(true)
    void refresh(true)
    const disconnect = subscribeSimulator(scheduleRefresh, setConnection)
    const poll = window.setInterval(scheduleRefresh, 8000)
    return () => {
      refreshAction.current = null
      controller.abort()
      disconnect()
      window.clearTimeout(scheduled)
      window.clearInterval(poll)
    }
  }, [])

  return { data, checked, refreshing, connection, reload: () => refreshAction.current?.() }
}
