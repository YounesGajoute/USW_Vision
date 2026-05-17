/**
 * WebSocket client (Socket.IO) for vision inspection — live feed, inspection control.
 * Connects via the same origin as the Next app; next.config rewrites /socket.io → Flask.
 */

import { io, Socket } from 'socket.io-client';
import type {
  InspectionResultEvent,
  LiveFrameEvent,
  SystemStatusEvent,
  ErrorEvent,
} from '@/types';

type EventCallback<T = unknown> = (data: T) => void;

/**
 * Socket.IO must reach Flask directly. Proxying Engine.IO through Next rewrites breaks
 * long-polling (hundreds of GET /socket.io/ per second). Prefer WebSocket + direct :5000.
 */
function resolveSocketUrl(explicitUrl?: string): string {
  if (explicitUrl) return explicitUrl;
  if (typeof window !== 'undefined') {
    const env = process.env.NEXT_PUBLIC_WS_URL;
    if (env) return env;
    const { protocol, hostname, port } = window.location;
    // Typical `next dev` / `next start` on 3000 — same host, Flask on 5000 (CORS must allow this Origin)
    if (port === '3000') {
      const apiPort = process.env.NEXT_PUBLIC_BACKEND_PORT || '5000';
      return `${protocol}//${hostname}:${apiPort}`;
    }
    return window.location.origin;
  }
  return 'http://127.0.0.1:5000';
}

class WebSocketClient {
  private socket: Socket | null = null;
  private url: string | undefined;
  private listenersBound = false;
  /** Keep trying while Flask on :5000 is still starting (e.g. slow boot / first build). */
  private static readonly reconnectAttempts = Infinity;

  private handlers: Map<string, Set<EventCallback>> = new Map();

  constructor(url?: string) {
    this.url = url;
  }

  /**
   * Drop the current socket without touching user-registered handlers on this client.
   */
  private teardownSocket(): void {
    if (!this.socket) return;
    this.socket.removeAllListeners();
    this.socket.io.removeAllListeners();
    this.socket.disconnect();
    this.socket = null;
    this.listenersBound = false;
  }

  /**
   * Connect (or reconnect) to the Socket.IO server.
   * Safe to call while disconnected or after errors; avoids duplicate listener stacks.
   */
  connect(): void {
    if (this.socket?.connected) {
      return;
    }

    this.teardownSocket();

    const connectUrl = resolveSocketUrl(this.url);
    if (process.env.NODE_ENV === 'development') {
      console.log('[Socket.IO] connecting to', connectUrl, 'path /socket.io');
    }

    const socketKey =
      typeof process !== 'undefined' ? process.env.NEXT_PUBLIC_VISION_SOCKETIO_KEY : undefined;
    this.socket = io(connectUrl, {
      path: '/socket.io',
      // Engine.IO server matches '/socket.io/'; keep slash after normalization
      addTrailingSlash: true,
      transports: ['websocket', 'polling'],
      upgrade: true,
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 8000,
      reconnectionAttempts: WebSocketClient.reconnectAttempts,
      timeout: 20000,
      // When slave uses remote.socketio_auth: inherit — same value as remote API key (exposed in bundle; LAN only)
      ...(socketKey ? { auth: { remoteKey: socketKey } } : {}),
    });

    this.setupListeners();
  }

  private setupListeners(): void {
    if (!this.socket || this.listenersBound) return;
    this.listenersBound = true;

    const socket = this.socket;

    socket.on('connect', () => {
      this.emit('connected', { status: 'connected' });
    });

    socket.on('disconnect', (reason) => {
      if (process.env.NODE_ENV === 'development') {
        console.log('[Socket.IO] disconnected:', reason);
      }
      this.emit('disconnected', { reason });
    });

    socket.on('connect_error', (error: Error & { description?: string }) => {
      const msg = error?.message || error?.description || String(error);
      if (process.env.NODE_ENV === 'development') {
        console.warn('[Socket.IO] connect_error:', msg);
      }
      this.emit('connect_error', { message: msg });
    });

    // With infinite attempts this should not fire; still notify if the manager gives up.
    socket.io.on('reconnect_failed', () => {
      console.warn(
        '[Socket.IO] reconnect_failed — is the Flask backend running on port 5000?'
      );
      this.emit('connection_failed', {
        error: 'Max reconnection attempts reached',
      });
    });

    socket.on('connection_status', (data) => {
      this.emit('connection_status', data);
    });

    socket.on('inspection_started', (data) => {
      this.emit('inspection_started', data);
    });

    socket.on('inspection_result', (data: InspectionResultEvent) => {
      this.emit('inspection_result', data);
    });

    socket.on('inspection_stopped', (data) => {
      this.emit('inspection_stopped', data);
    });

    socket.on('inspection_complete', (data) => {
      this.emit('inspection_complete', data);
    });

    socket.on('live_feed_started', (data) => {
      this.emit('live_feed_started', data);
    });

    socket.on('live_frame', (data: LiveFrameEvent) => {
      this.emit('live_frame', data);
    });

    socket.on('live_feed_stopped', (data) => {
      this.emit('live_feed_stopped', data);
    });

    socket.on('system_status', (data: SystemStatusEvent) => {
      this.emit('system_status', data);
    });

    socket.on('error', (data: ErrorEvent) => {
      console.error('[Socket.IO] server error event:', data);
      this.emit('error', data);
    });

    socket.on('warning', (data) => {
      console.warn('[Socket.IO] server warning:', data);
      this.emit('warning', data);
    });
  }

  disconnect(): void {
    this.teardownSocket();
  }

  /** True when the Engine.IO connection is up (matches socket.io-client semantics). */
  connected(): boolean {
    return this.socket?.connected === true;
  }

  startInspection(
    programId: number,
    continuous: boolean = true,
    templateId?: number
  ): void {
    if (!this.socket?.connected) {
      throw new Error('WebSocket not connected');
    }
    const payload: { programId: number; continuous: boolean; templateId?: number } = {
      programId,
      continuous,
    };
    if (templateId != null) {
      payload.templateId = templateId;
    }
    this.socket.emit('start_inspection', payload);
  }

  stopInspection(): void {
    if (!this.socket?.connected) {
      throw new Error('WebSocket not connected');
    }
    this.socket.emit('stop_inspection');
  }

  subscribeLiveFeed(fps: number = 20, fullResolution: boolean = false): void {
    if (!this.socket?.connected) {
      throw new Error('WebSocket not connected');
    }
    this.socket.emit('subscribe_live_feed', { fps, fullResolution });
  }

  /**
   * Subscribe after Socket.IO finishes connecting. Fixes a race where `connect()` is async
   * and `subscribeLiveFeed()` ran before `connected`, so the `connected` handler was never scheduled in time.
   * @param fullResolution Stream native IMX296 frames (1456×1088 PNG); use lower fps (≤6).
   */
  subscribeLiveFeedWhenReady(fps: number = 20, fullResolution: boolean = false): () => void {
    let cancelled = false;
    let sent = false;
    const send = () => {
      if (cancelled || sent || !this.socket?.connected) return;
      sent = true;
      this.socket.emit('subscribe_live_feed', { fps, fullResolution });
    };

    const onConnected = () => {
      this.off('connected', onConnected);
      send();
    };

    this.on('connected', onConnected);
    this.connect();
    queueMicrotask(() => {
      if (cancelled) return;
      if (this.socket?.connected) {
        this.off('connected', onConnected);
        send();
      }
    });

    return () => {
      cancelled = true;
      this.off('connected', onConnected);
    };
  }

  unsubscribeLiveFeed(): void {
    if (!this.socket?.connected) return;
    this.socket.emit('unsubscribe_live_feed');
  }

  requestSystemStatus(): void {
    if (!this.socket?.connected) {
      throw new Error('WebSocket not connected');
    }
    this.socket.emit('request_system_status');
  }

  on<T = unknown>(event: string, callback: EventCallback<T>): void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set());
    }
    this.handlers.get(event)!.add(callback as EventCallback);
  }

  off<T = unknown>(event: string, callback: EventCallback<T>): void {
    const handlers = this.handlers.get(event);
    if (handlers) {
      handlers.delete(callback as EventCallback);
      if (handlers.size === 0) {
        this.handlers.delete(event);
      }
    }
  }

  private emit<T>(event: string, data: T): void {
    const handlers = this.handlers.get(event);
    if (handlers) {
      handlers.forEach((handler) => {
        try {
          handler(data);
        } catch (error) {
          console.error(`Error in event handler for '${event}':`, error);
        }
      });
    }
  }

  clearHandlers(): void {
    this.handlers.clear();
  }
}

export const ws = new WebSocketClient();

export default WebSocketClient;
