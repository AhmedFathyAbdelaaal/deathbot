import { Injectable, signal } from '@angular/core';
import { tap } from 'rxjs';

import { ApiService } from './api.service';

const TOKEN_KEY = 'deathbot_token';

/** Holds the opaque session token (in localStorage) and tracks auth state. */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private _token = signal<string | null>(localStorage.getItem(TOKEN_KEY));
  readonly token = this._token.asReadonly();
  readonly isAuthed = signal<boolean>(!!localStorage.getItem(TOKEN_KEY));

  constructor(private api: ApiService) {}

  login(pin: string) {
    return this.api.checkPin(pin).pipe(
      tap((res) => {
        localStorage.setItem(TOKEN_KEY, res.token);
        this._token.set(res.token);
        this.isAuthed.set(true);
      }),
    );
  }

  logout() {
    localStorage.removeItem(TOKEN_KEY);
    this._token.set(null);
    this.isAuthed.set(false);
  }
}
