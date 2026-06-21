import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { Track } from '../models';
import { ApiService } from '../services/api.service';

@Component({
  selector: 'app-library',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="container col" style="gap:18px">
      <div class="row between">
        <h2 style="margin:0">Library</h2>
        <label class="upload primary" [class.busy]="uploading()">
          {{ uploading() ? 'Uploading…' : '+ Upload' }}
          <input type="file" accept=".mp3,.wav,.flac,.m4a" (change)="onFile($event)" [disabled]="uploading()" hidden />
        </label>
      </div>

      <input type="search" placeholder="Search by title or artist…" [(ngModel)]="q" (ngModelChange)="search()" />

      <p class="err small" *ngIf="error()">{{ error() }}</p>

      <div class="col" style="gap:8px">
        <div class="card item" *ngFor="let t of tracks()">
          <ng-container *ngIf="editingId() !== t.id; else editRow">
            <div class="item-meta">
              <div class="item-title">{{ t.title }}</div>
              <div class="faint small">{{ t.artist || 'Unknown artist' }}<span *ngIf="t.duration_seconds"> · <span class="mono">{{ fmt(t.duration_seconds) }}</span></span></div>
            </div>
            <div class="row" style="gap:4px">
              <button class="primary" (click)="addToQueue(t)">Queue</button>
              <button class="ghost" (click)="startEdit(t)">Edit</button>
              <button class="ghost danger" (click)="remove(t)">✕</button>
            </div>
          </ng-container>
          <ng-template #editRow>
            <div class="item-meta col" style="gap:6px">
              <input type="text" [(ngModel)]="editTitle" placeholder="Title" />
              <input type="text" [(ngModel)]="editArtist" placeholder="Artist" />
            </div>
            <div class="row" style="gap:4px">
              <button class="primary" (click)="saveEdit(t)">Save</button>
              <button class="ghost" (click)="editingId.set(null)">Cancel</button>
            </div>
          </ng-template>
        </div>
        <div class="faint" *ngIf="!tracks().length" style="padding:8px 2px">
          {{ q ? 'No matches.' : 'Library is empty — upload a track to get started.' }}
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .item { display: flex; align-items: center; gap: 12px; padding: 12px 14px; }
      .item-meta { flex: 1; min-width: 0; }
      .item-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 500; }
      .small { font-size: 12px; }
      .err { color: var(--signal-deep); }
      .upload {
        display: inline-block;
        background: var(--signal);
        color: #fff;
        border-radius: var(--radius);
        padding: 8px 14px;
        font-size: 14px;
        cursor: pointer;
      }
      .upload:hover { background: var(--signal-deep); }
      .upload.busy { opacity: 0.6; cursor: default; }
    `,
  ],
})
export class LibraryComponent implements OnInit {
  readonly tracks = signal<Track[]>([]);
  readonly uploading = signal(false);
  readonly editingId = signal<number | null>(null);
  readonly error = signal<string | null>(null);
  q = '';
  editTitle = '';
  editArtist = '';
  private searchTimer?: ReturnType<typeof setTimeout>;

  constructor(private api: ApiService) {}

  ngOnInit() {
    this.reload();
  }

  reload() {
    this.api.getLibrary(this.q.trim() || undefined).subscribe((ts) => this.tracks.set(ts));
  }

  search() {
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => this.reload(), 250);
  }

  onFile(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.uploading.set(true);
    this.error.set(null);
    this.api.upload(file).subscribe({
      next: (track) => {
        this.uploading.set(false);
        input.value = '';
        this.q = '';
        this.reload();
        // Open the just-uploaded track for ID3 review / correction.
        this.startEdit(track);
      },
      error: (err) => {
        this.uploading.set(false);
        input.value = '';
        this.error.set(
          err?.status === 413
            ? 'That file is over the 250 MB limit.'
            : err?.error?.detail || 'Upload failed.',
        );
      },
    });
  }

  startEdit(t: Track) {
    this.editingId.set(t.id);
    this.editTitle = t.title;
    this.editArtist = t.artist || '';
  }

  saveEdit(t: Track) {
    this.api
      .updateTrack(t.id, { title: this.editTitle.trim(), artist: this.editArtist.trim() })
      .subscribe(() => {
        this.editingId.set(null);
        this.reload();
      });
  }

  addToQueue(t: Track) {
    this.api.addToQueue({ track_id: t.id }).subscribe();
  }

  remove(t: Track) {
    if (confirm(`Delete "${t.title}" from the Library?`)) {
      this.api.deleteTrack(t.id).subscribe(() => this.reload());
    }
  }

  fmt(seconds: number | null | undefined): string {
    if (seconds == null) return '';
    const s = Math.floor(seconds);
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, '0')}`;
  }
}
