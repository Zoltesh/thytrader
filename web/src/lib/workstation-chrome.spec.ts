import { describe, expect, it } from 'vitest';

import {
	DEFAULT_CONTEXT_LABEL,
	RESEARCH_CONTEXT_LABEL,
	WORKSTATION_NAV,
	isWorkstationNavActive
} from './workstation-chrome';

describe('workstation chrome', () => {
	it('preserves the primary nav labels in route order', () => {
		expect(WORKSTATION_NAV.map((item) => item.label)).toEqual([
			'Portfolio',
			'Strategies',
			'Backtests',
			'Audit'
		]);
	});

	it('keeps environment labels static rather than implying health', () => {
		expect(DEFAULT_CONTEXT_LABEL).toBe('Local workstation');
		expect(RESEARCH_CONTEXT_LABEL).toBe('Research only');
	});

	it('marks Portfolio only on the dashboard route', () => {
		expect(isWorkstationNavActive('/', '/')).toBe(true);
		expect(isWorkstationNavActive('/', '/strategies')).toBe(false);
		expect(isWorkstationNavActive('/', '/audit')).toBe(false);
		expect(isWorkstationNavActive('/', null)).toBe(false);
	});

	it('keeps Strategies active on the library and builder routes', () => {
		expect(isWorkstationNavActive('/strategies', '/strategies')).toBe(true);
		expect(isWorkstationNavActive('/strategies', '/strategies/[id]')).toBe(true);
		expect(isWorkstationNavActive('/strategies', '/backtests')).toBe(false);
		expect(isWorkstationNavActive('/strategies', '/')).toBe(false);
	});

	it('marks Backtests and Audit on their own routes only', () => {
		expect(isWorkstationNavActive('/backtests', '/backtests')).toBe(true);
		expect(isWorkstationNavActive('/backtests', '/backtests/[id]')).toBe(true);
		expect(isWorkstationNavActive('/audit', '/audit')).toBe(true);
		expect(isWorkstationNavActive('/audit', '/backtests')).toBe(false);
	});
});
