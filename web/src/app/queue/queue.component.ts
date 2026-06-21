import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { NowPlaying, QueueItem } from '../models';
import { ApiService } from '../services/api.service';
import { WsService } from '../services/ws.service';

@Component({
  selector: 'app-queue',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="container col" style="gap:18px">
      <!-- Now playing -->
      <div class="card now" *ngIf="nowPlaying() as np; else idle">
        <div class="row between">
          <div class="np-meta">
            <div class="row" style="gap:8px">
              <span class="pulse-dot"></span>
              <span class="faint label">NOW PLAYING</span>
            </div>
            <h2 class="np-title">{{ np.title }}</h2>
            <div class="muted">{{ np.artist || 'Unknown artist' }}</div>
            <div class="faint mono small" *ngIf="np.requested_by">added by {{ np.requested_by }}</div>
          </div>
          <button class="danger" (click)="skipConfirm()">Skip ›</button>
        </div>
        <div class="progress">
          <div class="bar"><div class="fill" [style.width.%]="progressPct()"></div></div>
          <div class="row between mono small faint">
            <span>{{ fmt(ws.position()) }}</span>
            <span>{{ fmt(np.duration) }}</span>
          </div>
        </div>
      </div>
      <ng-template #idle>
        <div class="card now idle-card">
          <span class="pulse-dot idle"></span>
          <span class="muted">Nothing playing right now.</span>
        </div>
      </ng-template>

      <!-- Add by link -->
      <div class="card">
        <form class="row" (ngSubmit)="addLink()">
          <input type="text" placeholder="Paste a SoundCloud / YouTube / Spotify link or search…"
                 [(ngModel)]="link" name="link" [disabled]="adding()" />
          <button class="primary" type="submit" [disabled]="adding() || !link.trim()">Add</button>
        </form>
        <p class="faint small" style="margin:8px 0 0">Pasted links play once — they aren't saved to the Library.</p>
      </div>

      <!-- Controls -->
      <div class="row" style="gap:8px">
        <button (click)="shuffle()" [disabled]="!items().length">Shuffle</button>
        <button class="danger" (click)="stopConfirm()">Stop &amp; clear</button>
        <span class="spacer"></span>
        <span class="faint mono small">{{ items().length }} up next</span>
      </div>

      <!-- Up next -->
      <div class="col" style="gap:8px">
        <div class="card item" *ngFor="let it of items(); let i = index">
          <span class="mono pos">{{ i + 1 }}</span>
          <div class="item-meta">
            <div class="item-title">{{ it.title || it.source_url || 'Untitled' }}</div>
            <div class="faint small" *ngIf="it.artist">{{ it.artist }}</div>
          </div>
          <div class="row" style="gap:4px">
            <button class="ghost" (click)="move(i, i - 1)" [disabled]="i === 0" title="Up">↑</button>
            <button class="ghost" (click)="move(i, i + 1)" [disabled]="i === items().length - 1" title="Down">↓</button>
            <button class="ghost danger" (click)="remove(it)" title="Remove">✕</button>
          </div>
        </div>
        <div class="faint" *ngIf="!items().length" style="padding:8px 2px">Queue is empty.</div>
      </div>
    </div>
  `,
  styles: [
    `
      .now { display: flex; flex-direction: column; gap: 14px; }
      .label { font-size: 11px; letter-spacing: 0.12em; }
      .np-title { margin: 8px 0 2px; font-size: 20px; }
      .small { font-size: 12px; }
      .idle-card { flex-direction: row; align-items: center; gap: 10px; }
      .progress { display: flex; flex-direction: column; gap: 6px; }
      .bar { height: 6px; background: var(--paper-dim); border-radius: 99px; overflow: hidden; }
      .fill { height: 100%; background: var(--signal); transition: width 0.5s linear; }
      .item { display: flex; align-items: center; gap: 12px; padding: 10px 14px; }
      .pos { color: var(--ink-faint); width: 22px; text-align: right; }
      .item-meta { flex: 1; min-width: 0; }
      .item-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    `,
  ],
})
export class QueueComponent implements OnInit, OnDestroy {
  readonly nowPlaying = signal<NowPlaying | null>(null);
  readonly items = signal<QueueItem[]>([]);
  readonly adding = signal(false);
  link = '';
  private unsub: (() => void) | null = null;

  constructor(private api: ApiService, public ws: WsService) {}

  ngOnInit() {
    this.reload();
    this.unsub = this.ws.onChange(() => this.reload());
  }

  ngOnDestroy() {
    this.unsub?.();
  }

  reload() {
    this.api.getQueue().subscribe((state) => {
      this.nowPlaying.set(state.now_playing);
      this.items.set(state.queue);
    });
  }

  progressPct(): number {
    const pos = this.ws.position();
    const dur = this.nowPlaying()?.duration;
    if (!dur || pos == null) return 0;
    return Math.min(100, (pos / dur) * 100);
  }

  addLink() {
    const link = this.link.trim();
    if (!link) return;
    this.adding.set(true);
    this.api.addToQueue({ source_url: link }).subscribe({
      next: () => {
        this.link = '';
        this.adding.set(false);
        this.reload();
      },
      error: () => this.adding.set(false),
    });
  }

  remove(it: QueueItem) {
    this.api.removeFromQueue(it.id).subscribe(() => this.reload());
  }

  move(from: number, to: number) {
    const it = this.items()[from];
    if (!it) return;
    this.api.moveInQueue(it.id, to).subscribe(() => this.reload());
  }

  skipConfirm() {
    if (confirm('Skip the current track?')) {
      this.api.skip().subscribe(() => this.reload());
    }
  }

  shuffle() {
    this.api.shuffleQueue().subscribe(() => this.reload());
  }

  stopConfirm() {
    if (confirm('Stop playback and clear the entire queue?')) {
      this.api.stopQueue().subscribe(() => this.reload());
    }
  }

  fmt(seconds: number | null | undefined): string {
    if (seconds == null) return '–:––';
    const s = Math.max(0, Math.floor(seconds));
    const m = Math.floor(s / 60);
    const r = s % 60;
    const h = Math.floor(m / 60);
    const mm = h ? String(m % 60).padStart(2, '0') : String(m);
    return h ? `${h}:${mm}:${String(r).padStart(2, '0')}` : `${mm}:${String(r).padStart(2, '0')}`;
  }
}
