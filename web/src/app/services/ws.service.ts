import { Inject, Injectable, signal } from '@angular/core';

import { APP_CONFIG, AppConfig } from '../config';
import { NowPlaying } from '../models';
import { AuthService } from './auth.service';

/** Live state connection. Exposes the now-playing track and playback position
 *  as signals, and notifies listeners on any queue change so views reload.
 *  Reconnects automatically. */
@Injectable({ providedIn: 'root' })
export class WsService {
  private wsBase: string;
  private socket?: WebSocket;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private listeners = new Set<() => void>();

  readonly nowPlaying = signal<NowPlaying | null>(null);
  readonly position = signal<number | null>(null);
  readonly connected = signal(false);

  constructor(@Inject(APP_CONFIG) cfg: AppConfig, private auth: AuthService) {
    this.wsBase = cfg.wsBaseUrl.replace(/\/$/, '');
  }

  connect() {
    const token = this.auth.token();
    if (!token) return;
    this.disconnect();

    const sock = new WebSocket(`${this.wsBase}/ws?token=${encodeURIComponent(token)}`);
    this.socket = sock;
    sock.onopen = () => this.connected.set(true);
    sock.onmessage = (ev) => this.handle(ev);
    sock.onerror = () => sock.close();
    sock.onclose = () => {
      this.connected.set(false);
      if (this.socket === sock) this.scheduleReconnect();
    };
  }

  private handle(ev: MessageEvent) {
    let msg: { type: string; track?: NowPlaying | null; seconds?: number };
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (msg.type === 'now_playing') {
      this.nowPlaying.set(msg.track ?? null);
      this.position.set(msg.track ? 0 : null);
      this.notify();
    } else if (msg.type === 'position') {
      this.position.set(msg.seconds ?? null);
    } else if (msg.type === 'queue_changed') {
      this.notify();
    }
  }

  /** Subscribe to "something changed, reload" pings. Returns an unsubscribe fn. */
  onChange(cb: () => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
  private notify() {
    this.listeners.forEach((cb) => cb());
  }

  private scheduleReconnect() {
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), 3000);
  }

  disconnect() {
    clearTimeout(this.reconnectTimer);
    if (this.socket) {
      const s = this.socket;
      this.socket = undefined;
      s.onclose = null;
      try {
        s.close();
      } catch {
        /* ignore */
      }
    }
    this.connected.set(false);
  }
}
