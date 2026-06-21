import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { Playlist, PlaylistDetail, Track } from '../models';
import { ApiService } from '../services/api.service';

@Component({
  selector: 'app-playlists',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="container col" style="gap:18px">
      <!-- List view -->
      <ng-container *ngIf="!selected()">
        <h2 style="margin:0">Playlists</h2>
        <div class="card">
          <form class="row" (ngSubmit)="create()">
            <input type="text" placeholder="New playlist name…" [(ngModel)]="newName" name="newName" />
            <button class="primary" type="submit" [disabled]="!newName.trim()">Create</button>
          </form>
        </div>
        <div class="col" style="gap:8px">
          <div class="card item" *ngFor="let pl of playlists()">
            <div class="item-meta" (click)="open(pl)" style="cursor:pointer">
              <div class="item-title">{{ pl.name }}</div>
              <div class="faint small mono">{{ pl.track_count || 0 }} track(s)</div>
            </div>
            <div class="row" style="gap:4px">
              <button class="primary" (click)="queueAll(pl)" [disabled]="!pl.track_count">Queue all</button>
              <button class="ghost" (click)="open(pl)">Open</button>
              <button class="ghost danger" (click)="remove(pl)">✕</button>
            </div>
          </div>
          <div class="faint" *ngIf="!playlists().length" style="padding:8px 2px">No playlists yet.</div>
        </div>
      </ng-container>

      <!-- Detail view -->
      <ng-container *ngIf="selected() as pl">
        <div class="row between">
          <button class="ghost" (click)="closeDetail()">‹ Playlists</button>
          <div class="row" style="gap:4px">
            <button class="primary" (click)="queueAllDetail(pl)" [disabled]="!pl.tracks.length">Queue all</button>
            <button class="ghost" (click)="rename(pl)">Rename</button>
          </div>
        </div>
        <h2 style="margin:0">{{ pl.name }}</h2>

        <div class="col" style="gap:8px">
          <div class="card item" *ngFor="let t of pl.tracks; let i = index">
            <span class="mono pos">{{ i + 1 }}</span>
            <div class="item-meta">
              <div class="item-title">{{ t.title }}</div>
              <div class="faint small" *ngIf="t.artist">{{ t.artist }}</div>
            </div>
            <div class="row" style="gap:4px">
              <button class="ghost" (click)="move(pl, i, i - 1)" [disabled]="i === 0">↑</button>
              <button class="ghost" (click)="move(pl, i, i + 1)" [disabled]="i === pl.tracks.length - 1">↓</button>
              <button class="ghost danger" (click)="removeTrack(pl, t.playlist_track_id)">✕</button>
            </div>
          </div>
          <div class="faint" *ngIf="!pl.tracks.length" style="padding:8px 2px">Empty — add tracks from the Library below.</div>
        </div>

        <!-- Add from library -->
        <div class="card col" style="gap:10px">
          <strong>Add from Library</strong>
          <input type="search" placeholder="Search the Library…" [(ngModel)]="pickerQ" (ngModelChange)="searchPicker()" />
          <div class="col" style="gap:6px; max-height:280px; overflow:auto">
            <div class="row between picker-row" *ngFor="let tr of picker()">
              <div class="item-meta">
                <div class="item-title">{{ tr.title }}</div>
                <div class="faint small" *ngIf="tr.artist">{{ tr.artist }}</div>
              </div>
              <button class="ghost" (click)="addTrack(pl, tr)">+ Add</button>
            </div>
            <div class="faint small" *ngIf="!picker().length" style="padding:4px 2px">No tracks found.</div>
          </div>
        </div>
      </ng-container>
    </div>
  `,
  styles: [
    `
      .item { display: flex; align-items: center; gap: 12px; padding: 12px 14px; }
      .item-meta { flex: 1; min-width: 0; }
      .item-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 500; }
      .small { font-size: 12px; }
      .pos { color: var(--ink-faint); width: 22px; text-align: right; }
      .picker-row { padding: 6px 8px; border-radius: var(--radius); }
      .picker-row:hover { background: var(--paper-dim); }
    `,
  ],
})
export class PlaylistsComponent implements OnInit {
  readonly playlists = signal<Playlist[]>([]);
  readonly selected = signal<PlaylistDetail | null>(null);
  readonly picker = signal<Track[]>([]);
  newName = '';
  pickerQ = '';
  private pickerTimer?: ReturnType<typeof setTimeout>;

  constructor(private api: ApiService) {}

  ngOnInit() {
    this.reload();
  }

  reload() {
    this.api.getPlaylists().subscribe((pls) => this.playlists.set(pls));
  }

  create() {
    const name = this.newName.trim();
    if (!name) return;
    this.api.createPlaylist(name).subscribe(() => {
      this.newName = '';
      this.reload();
    });
  }

  open(pl: Playlist) {
    this.api.getPlaylist(pl.id).subscribe((detail) => {
      this.selected.set(detail);
      this.loadPicker();
    });
  }

  reopen(pl: PlaylistDetail) {
    this.api.getPlaylist(pl.id).subscribe((detail) => this.selected.set(detail));
  }

  closeDetail() {
    this.selected.set(null);
    this.reload();
  }

  remove(pl: Playlist) {
    if (confirm(`Delete playlist "${pl.name}"?`)) {
      this.api.deletePlaylist(pl.id).subscribe(() => this.reload());
    }
  }

  rename(pl: PlaylistDetail) {
    const name = prompt('Rename playlist', pl.name);
    if (name && name.trim()) {
      this.api.renamePlaylist(pl.id, name.trim()).subscribe(() => this.reopen(pl));
    }
  }

  queueAll(pl: Playlist) {
    this.api.queuePlaylist(pl.id).subscribe((r) => alert(`Queued ${r.queued} track(s).`));
  }

  queueAllDetail(pl: PlaylistDetail) {
    this.api.queuePlaylist(pl.id).subscribe((r) => alert(`Queued ${r.queued} track(s).`));
  }

  move(pl: PlaylistDetail, from: number, to: number) {
    const t = pl.tracks[from];
    if (!t) return;
    this.api.movePlaylistTrack(pl.id, t.playlist_track_id, to).subscribe(() => this.reopen(pl));
  }

  removeTrack(pl: PlaylistDetail, playlistTrackId: number) {
    this.api.removePlaylistTrack(pl.id, playlistTrackId).subscribe(() => this.reopen(pl));
  }

  addTrack(pl: PlaylistDetail, tr: Track) {
    this.api.addPlaylistTrack(pl.id, tr.id).subscribe(() => this.reopen(pl));
  }

  loadPicker() {
    this.api.getLibrary(this.pickerQ.trim() || undefined).subscribe((ts) => this.picker.set(ts));
  }

  searchPicker() {
    clearTimeout(this.pickerTimer);
    this.pickerTimer = setTimeout(() => this.loadPicker(), 250);
  }
}
