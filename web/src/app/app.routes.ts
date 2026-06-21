import { Routes } from '@angular/router';

import { authGuard } from './services/auth.guard';

export const routes: Routes = [
  {
    path: 'pin',
    loadComponent: () => import('./pin/pin.component').then((m) => m.PinComponent),
  },
  {
    path: 'queue',
    canActivate: [authGuard],
    loadComponent: () => import('./queue/queue.component').then((m) => m.QueueComponent),
  },
  {
    path: 'library',
    canActivate: [authGuard],
    loadComponent: () => import('./library/library.component').then((m) => m.LibraryComponent),
  },
  {
    path: 'playlists',
    canActivate: [authGuard],
    loadComponent: () => import('./playlists/playlists.component').then((m) => m.PlaylistsComponent),
  },
  { path: '', pathMatch: 'full', redirectTo: 'queue' },
  { path: '**', redirectTo: 'queue' },
];
