import { InjectionToken } from '@angular/core';

/** Resolved at runtime from /assets/config.json, which the container entrypoint
 *  renders from the API_BASE_URL / WS_BASE_URL env vars. */
export interface AppConfig {
  apiBaseUrl: string;
  wsBaseUrl: string;
}

export const APP_CONFIG = new InjectionToken<AppConfig>('APP_CONFIG');
