/**
 * Generic realtime subscription over the backend's `/ws` bridge
 * (see api/routers/ws.py). Reusable for any topic — ingestion progress is the
 * first consumer.
 *
 * Pass `topic = null` to stay disconnected. The master key is read from the
 * token store and sent as a query param (browsers can't set WS headers). The
 * socket reconnects with capped backoff and is always closed on unmount.
 */

import { useEffect, useRef, useState } from 'react'
import { getMasterKey } from '../api/tokenStore'

export type ChannelStatus = 'connecting' | 'open' | 'closed'

/** Same-origin WS URL; Vite proxies `/ws` to the API (prod: nginx must too). */
function wsUrl(topic: string, key: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws?topic=${encodeURIComponent(
    topic,
  )}&key=${encodeURIComponent(key)}`
}

export function useChannel<T = unknown>(
  topic: string | null,
  onMessage?: (msg: T) => void,
): { lastMessage: T | null; status: ChannelStatus } {
  const [lastMessage, setLastMessage] = useState<T | null>(null)
  const [status, setStatus] = useState<ChannelStatus>('closed')
  // Hold the latest callback in a ref so a changing handler identity doesn't
  // tear down and rebuild the socket.
  const cbRef = useRef(onMessage)
  cbRef.current = onMessage

  useEffect(() => {
    const key = getMasterKey()
    if (!topic || !key) {
      setStatus('closed')
      return
    }

    let ws: WebSocket | null = null
    let retry = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    let unmounted = false

    const connect = () => {
      setStatus('connecting')
      ws = new WebSocket(wsUrl(topic, key))
      ws.onopen = () => {
        retry = 0
        setStatus('open')
      }
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as T
          setLastMessage(msg)
          cbRef.current?.(msg)
        } catch {
          // Ignore non-JSON frames.
        }
      }
      ws.onclose = () => {
        setStatus('closed')
        if (unmounted) return
        const delay = Math.min(8000, 500 * 2 ** retry) // 0.5s → 8s
        retry += 1
        timer = setTimeout(connect, delay)
      }
      ws.onerror = () => ws?.close()
    }
    connect()

    return () => {
      unmounted = true
      if (timer) clearTimeout(timer)
      ws?.close()
    }
  }, [topic])

  return { lastMessage, status }
}
