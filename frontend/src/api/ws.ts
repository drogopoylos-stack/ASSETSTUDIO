// Resilient WebSocket connection that funnels live events into the store.
import { useStore } from "../store/useStore";
import type { WSEvent } from "../types";

let socket: WebSocket | null = null;
let retry = 0;

function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

export function connectWS() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING))
    return;
  const { setWsConnected, handleEvent } = useStore.getState();
  try {
    socket = new WebSocket(wsUrl());
  } catch {
    scheduleReconnect();
    return;
  }
  socket.onopen = () => {
    retry = 0;
    setWsConnected(true);
  };
  socket.onmessage = (e) => {
    try {
      handleEvent(JSON.parse(e.data) as WSEvent);
    } catch {
      /* ignore malformed */
    }
  };
  socket.onclose = () => {
    setWsConnected(false);
    scheduleReconnect();
  };
  socket.onerror = () => socket?.close();
}

function scheduleReconnect() {
  retry = Math.min(retry + 1, 10);
  setTimeout(connectWS, Math.min(500 * retry, 5000));
}
