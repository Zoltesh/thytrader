/**
 * Theme and per-viewer shell preferences (ADR 0079).
 *
 * The inline boot script in `app.html` applies the same resolution before
 * first paint; keep `THEME_STORAGE_KEY` and the resolution rule in sync with
 * it. Storage can be unavailable (private windows, blocked site data), so
 * every read and write is wrapped and falls back to defaults.
 */

export type Theme = 'dark' | 'light';

export const THEME_STORAGE_KEY = 'thytrader.theme';
export const AGENT_PANEL_STORAGE_KEY = 'thytrader.agent-panel';
export const SYSTEM_GROUP_STORAGE_KEY = 'thytrader.system-group';

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function safeStorage(): StorageLike | null {
	try {
		return typeof localStorage === 'undefined' ? null : localStorage;
	} catch {
		return null;
	}
}

export function isTheme(value: unknown): value is Theme {
	return value === 'dark' || value === 'light';
}

/** The operator's explicit choice, or null when they have not chosen. */
export function readStoredTheme(storage: StorageLike | null = safeStorage()): Theme | null {
	try {
		const value = storage?.getItem(THEME_STORAGE_KEY) ?? null;
		return isTheme(value) ? value : null;
	} catch {
		return null;
	}
}

/** An explicit choice wins; otherwise follow the OS colour-scheme preference. Dark by default. */
export function resolveTheme(stored: Theme | null, prefersLight: boolean): Theme {
	return stored ?? (prefersLight ? 'light' : 'dark');
}

export function prefersLightScheme(): boolean {
	try {
		return window.matchMedia('(prefers-color-scheme: light)').matches;
	} catch {
		return false;
	}
}

export function currentTheme(): Theme {
	const attr = document.documentElement.getAttribute('data-theme');
	return isTheme(attr) ? attr : resolveTheme(readStoredTheme(), prefersLightScheme());
}

export function applyTheme(theme: Theme): void {
	document.documentElement.setAttribute('data-theme', theme);
}

export function storeTheme(theme: Theme, storage: StorageLike | null = safeStorage()): void {
	try {
		storage?.setItem(THEME_STORAGE_KEY, theme);
	} catch {
		// Storage blocked: the choice still applies for this page view.
	}
}

export function readFlag(
	key: string,
	fallback: boolean,
	storage: StorageLike | null = safeStorage()
): boolean {
	try {
		const value = storage?.getItem(key) ?? null;
		if (value === 'true') return true;
		if (value === 'false') return false;
		return fallback;
	} catch {
		return fallback;
	}
}

export function storeFlag(
	key: string,
	value: boolean,
	storage: StorageLike | null = safeStorage()
): void {
	try {
		storage?.setItem(key, value ? 'true' : 'false');
	} catch {
		// Storage blocked: keep the in-memory state only.
	}
}

/**
 * Read the value of a design token from the document root, for canvas
 * libraries (Lightweight Charts) that cannot consume CSS custom properties.
 */
export function readToken(name: `--${string}`, fallback: string): string {
	try {
		const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
		return value === '' ? fallback : value;
	} catch {
		return fallback;
	}
}
