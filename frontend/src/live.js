const eventNames = ['simulation.tick', 'allocation.status_changed', 'inventory.updated', 'simulator.notice']

export function subscribeSimulator(onRefresh, onConnection, EventStream = globalThis.EventSource) {
  let source
  let retry
  let closed = false
  let delay = 5000

  function connect() {
    const stream = new EventStream('/api/v1/stream')
    source = stream
    stream.onopen = () => {
      if (closed || source !== stream) return
      delay = 5000
      onConnection('live')
      onRefresh()
    }
    stream.onerror = () => {
      if (closed || source !== stream) return
      source = undefined
      stream.close()
      onConnection('polling')
      onRefresh()
      retry = setTimeout(connect, delay)
      delay = Math.min(30000, delay * 2)
    }
    for (const name of eventNames) {
      stream.addEventListener(name, () => {
        if (!closed && source === stream) onRefresh()
      })
    }
  }

  connect()
  return () => {
    closed = true
    clearTimeout(retry)
    source?.close()
  }
}
