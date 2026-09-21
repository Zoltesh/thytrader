import { expect, test } from '@playwright/test';

export { expect, test };

/** Match GET/POST `/api/v1/strategies` even when the client sends `?limit=` / `?cursor=`. */
export function isStrategyLibraryRequest(url: URL): boolean {
	return url.pathname === '/api/v1/strategies' || url.pathname === '/api/v1/strategies/';
}
