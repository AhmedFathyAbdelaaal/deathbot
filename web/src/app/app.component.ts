import { Component, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService } from './services/auth.service';
import { WsService } from './services/ws.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <header class="topbar" *ngIf="auth.isAuthed()">
      <div class="brand">
        <span class="pulse-dot" [class.idle]="!ws.nowPlaying()"></span>
        <span class="name">Death Bot</span>
      </div>
      <nav>
        <a routerLink="/queue" routerLinkActive="active">Queue</a>
        <a routerLink="/library" routerLinkActive="active">Library</a>
        <a routerLink="/playlists" routerLinkActive="active">Playlists</a>
      </nav>
      <span class="spacer"></span>
      <button class="ghost" (click)="logout()">Sign out</button>
    </header>

    <main>
      <router-outlet></router-outlet>
    </main>
  `,
  styles: [
    `
      .topbar {
        display: flex;
        align-items: center;
        gap: 22px;
        padding: 12px 18px;
        border-bottom: 1px solid var(--line);
        background: var(--paper);
        position: sticky;
        top: 0;
        z-index: 10;
      }
      .brand {
        display: flex;
        align-items: center;
        gap: 9px;
      }
      .brand .name {
        font-family: var(--font-display);
        font-weight: 700;
        font-size: 17px;
      }
      nav {
        display: flex;
        gap: 4px;
      }
      nav a {
        color: var(--ink-soft);
        padding: 6px 12px;
        border-radius: var(--radius);
        font-weight: 500;
      }
      nav a:hover {
        background: var(--paper-dim);
        text-decoration: none;
      }
      nav a.active {
        color: var(--signal-deep);
        background: var(--signal-dim);
      }
    `,
  ],
})
export class AppComponent {
  constructor(public auth: AuthService, public ws: WsService, private router: Router) {
    // Keep the live socket tied to auth state.
    effect(() => {
      if (this.auth.isAuthed()) {
        this.ws.connect();
      } else {
        this.ws.disconnect();
      }
    });
  }

  logout() {
    this.auth.logout();
    this.router.navigate(['/pin']);
  }
}
