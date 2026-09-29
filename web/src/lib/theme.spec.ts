import { describe, expect, it } from 'vitest';

import {
	THEME_STORAGE_KEY,
	isTheme,
	readFlag,
	readStoredTheme,
	resolveTheme,
	storeFlag,
	storeTheme
} from './theme';

function memoryStorage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
	const values = new Map<string, string>();
	return {
		getItem: (key) => values.get(key) ?? null,
		setItem: (key, value) => void values.set(key, value),
		removeItem: (key) => void values.delete(key)
	};
}

const throwingStorage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> = {
	getItem: () => {
		throw new Error('blocked');
	},
	setItem: () => {
		throw new Error('blocked');
	},
	removeItem: () => {
		throw new Error('blocked');
	}
};

describe('theme preferences', () => {
	it('lets an explicit choice win over the OS preference, dark by default', () => {
		expect(resolveTheme(null, false)).toBe('dark');
		expect(resolveTheme(null, true)).toBe('light');
		expect(resolveTheme('dark', true)).toBe('dark');
		expect(resolveTheme('light', false)).toBe('light');
	});

	it('round-trips a stored theme and ignores junk values', () => {
		const storage = memoryStorage();
		expect(readStoredTheme(storage)).toBeNull();
		storeTheme('light', storage);
		expect(storage.getItem(THEME_STORAGE_KEY)).toBe('light');
		expect(readStoredTheme(storage)).toBe('light');
		storage.setItem(THEME_STORAGE_KEY, 'sepia');
		expect(readStoredTheme(storage)).toBeNull();
		expect(isTheme('sepia')).toBe(false);
	});

	it('falls back quietly when storage throws', () => {
		expect(readStoredTheme(throwingStorage)).toBeNull();
		expect(() => storeTheme('dark', throwingStorage)).not.toThrow();
		expect(readFlag('k', true, throwingStorage)).toBe(true);
		expect(() => storeFlag('k', false, throwingStorage)).not.toThrow();
	});

	it('stores boolean flags as text and defaults when absent', () => {
		const storage = memoryStorage();
		expect(readFlag('agent', false, storage)).toBe(false);
		storeFlag('agent', true, storage);
		expect(readFlag('agent', false, storage)).toBe(true);
		storeFlag('agent', false, storage);
		expect(readFlag('agent', true, storage)).toBe(false);
	});
});
