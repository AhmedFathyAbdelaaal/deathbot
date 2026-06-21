import { HttpClient } from '@angular/common/http';
import { Inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { APP_CONFIG, AppConfig } from '../config';
import {
  Playlist,
  PlaylistDetail,
  QueueState,
  SessionUser,
  Track,
} from '../models';

/** Thin typed wrapper over the bot's REST API. The base URL comes from the
 *  runtime config; the auth token is attached by the HTTP interceptor. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private base: string;

  constructor(private http: HttpClient, @Inject(APP_CONFIG) cfg: AppConfig) {
    this.base = cfg.apiBaseUrl.replace(/\/$/, '');
  }

  // ── Auth ──
  checkPin(pin: string): Observable<{ token: string; user: SessionUser }> {
    return this.http.post<{ token: string; user: SessionUser }>(`${this.base}/auth/pin`, { pin });
  }
  me(): Observable<SessionUser> {
    return this.http.get<SessionUser>(`${this.base}/auth/me`);
  }

  // ── Queue ──
  getQueue(): Observable<QueueState> {
    return this.http.get<QueueState>(`${this.base}/queue`);
  }
  addToQueue(body: { track_id?: number; source_url?: string; title?: string; artist?: string }) {
    return this.http.post<{ id: number; position: number; started_now: boolean }>(`${this.base}/queue`, body);
  }
  removeFromQueue(id: number) {
    return this.http.delete(`${this.base}/queue/${id}`);
  }
  moveInQueue(id: number, position: number) {
    return this.http.post(`${this.base}/queue/${id}/move`, { position });
  }
  skip() {
    return this.http.post<{ skipped: boolean }>(`${this.base}/queue/skip`, {});
  }
  shuffleQueue() {
    return this.http.post<{ count: number }>(`${this.base}/queue/shuffle`, {});
  }
  stopQueue() {
    return this.http.post<{ ok: boolean }>(`${this.base}/queue/stop`, {});
  }

  // ── Library ──
  getLibrary(q?: string): Observable<Track[]> {
    const url = q ? `${this.base}/library?q=${encodeURIComponent(q)}` : `${this.base}/library`;
    return this.http.get<Track[]>(url);
  }
  upload(file: File, title?: string, artist?: string): Observable<Track> {
    const form = new FormData();
    form.append('file', file);
    if (title) form.append('title', title);
    if (artist) form.append('artist', artist);
    return this.http.post<Track>(`${this.base}/library`, form);
  }
  updateTrack(id: number, body: { title?: string; artist?: string }) {
    return this.http.patch<Track>(`${this.base}/library/${id}`, body);
  }
  deleteTrack(id: number) {
    return this.http.delete(`${this.base}/library/${id}`);
  }

  // ── Playlists ──
  getPlaylists(): Observable<Playlist[]> {
    return this.http.get<Playlist[]>(`${this.base}/playlists`);
  }
  createPlaylist(name: string): Observable<Playlist> {
    return this.http.post<Playlist>(`${this.base}/playlists`, { name });
  }
  getPlaylist(id: number): Observable<PlaylistDetail> {
    return this.http.get<PlaylistDetail>(`${this.base}/playlists/${id}`);
  }
  renamePlaylist(id: number, name: string) {
    return this.http.patch<Playlist>(`${this.base}/playlists/${id}`, { name });
  }
  deletePlaylist(id: number) {
    return this.http.delete(`${this.base}/playlists/${id}`);
  }
  addPlaylistTrack(playlistId: number, trackId: number) {
    return this.http.post(`${this.base}/playlists/${playlistId}/tracks`, { track_id: trackId });
  }
  removePlaylistTrack(playlistId: number, playlistTrackId: number) {
    return this.http.delete(`${this.base}/playlists/${playlistId}/tracks/${playlistTrackId}`);
  }
  movePlaylistTrack(playlistId: number, playlistTrackId: number, position: number) {
    return this.http.post(`${this.base}/playlists/${playlistId}/tracks/${playlistTrackId}/move`, { position });
  }
  queuePlaylist(playlistId: number) {
    return this.http.post<{ queued: number }>(`${this.base}/playlists/${playlistId}/queue`, {});
  }
}
