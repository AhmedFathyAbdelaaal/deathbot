import { Component, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../services/auth.service';

@Component({
  selector: 'app-pin',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="wrap">
      <div class="card panel">
        <div class="brand">
          <span class="pulse-dot"></span>
          <h1>Death Bot</h1>
        </div>
        <p class="muted">
          Enter the pin from <span class="mono">/website</span> in Discord.
        </p>
        <form (ngSubmit)="submit()">
          <input
            class="mono pin-input"
            type="text"
            inputmode="numeric"
            autocomplete="off"
            placeholder="••••"
            [(ngModel)]="pin"
            name="pin"
            [disabled]="busy()"
            autofocus
          />
          <button class="primary" type="submit" [disabled]="busy() || !pin.trim()">
            {{ busy() ? 'Checking…' : 'Enter' }}
          </button>
        </form>
        <p class="err" *ngIf="error()">{{ error() }}</p>
      </div>
    </div>
  `,
  styles: [
    `
      .wrap {
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 20px;
      }
      .panel {
        width: 100%;
        max-width: 360px;
        padding: 28px;
      }
      .brand {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 6px;
      }
      .brand h1 {
        margin: 0;
        font-size: 22px;
      }
      form {
        display: flex;
        flex-direction: column;
        gap: 12px;
        margin-top: 18px;
      }
      .pin-input {
        font-size: 22px;
        letter-spacing: 0.3em;
        text-align: center;
        padding: 12px;
      }
      .err {
        color: var(--signal-deep);
        font-size: 13px;
        margin: 12px 0 0;
      }
    `,
  ],
})
export class PinComponent {
  pin = '';
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);

  constructor(private auth: AuthService, private router: Router) {}

  submit() {
    const pin = this.pin.trim();
    if (!pin) return;
    this.busy.set(true);
    this.error.set(null);
    this.auth.login(pin).subscribe({
      next: () => this.router.navigate(['/queue']),
      error: () => {
        this.error.set('Invalid pin. Run /website in Discord for a fresh one.');
        this.busy.set(false);
      },
    });
  }
}
