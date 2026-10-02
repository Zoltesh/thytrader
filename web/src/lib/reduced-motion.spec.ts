import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const REDUCE = '@media (prefers-reduced-motion: reduce)';
const css = readFileSync(new URL('../app.css', import.meta.url), 'utf8');

describe('app.css reduced motion', () => {
	it('declares the reduced-motion rule after every animated primitive', () => {
		const reduce = css.lastIndexOf(REDUCE);
		expect(reduce).toBeGreaterThan(-1);
		expect(reduce).toBeGreaterThan(css.lastIndexOf('animation: shimmer'));
		expect(reduce).toBeGreaterThan(css.lastIndexOf('animation: spin'));
		expect(css.indexOf(REDUCE)).toBe(reduce);
	});

	it('stills skeletons and spinners app-wide, including component-scoped copies', () => {
		const block = css.slice(css.lastIndexOf(REDUCE));
		expect(block).toMatch(/\.skeleton,\s*\.spinning\s*\{\s*animation: none !important;/);
	});
});
