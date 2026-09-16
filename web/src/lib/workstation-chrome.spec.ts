import { describe, expect, it } from 'vitest';

import {
	DEFAULT_CONTEXT_LABEL,
	OPERATOR_CHAT_CONTEXT_LABEL,
	RESEARCH_CONTEXT_LABEL,
	SETTINGS_CONTEXT_LABEL,
	WORKSTATION_NAV,
	isWorkstationNavActive
} from './workstation-chrome';

describe('workstation chrome', () => {
	it('preserves the primary nav labels in route order', () => {
		expect(WORKSTATION_NAV.map((item) => item.label)).toEqual([
			'Portfolio',
			'Strategies',
			'Backtests',
			'Research',
			'Deploy',
			'Trade',
			'Journals',
			'Chat',
			'Audit',
			'Memory',
			'Settings'
		]);
	});

	it('keeps environment labels static rather than implying health', () => {
		expect(DEFAULT_CONTEXT_LABEL).toBe('Local workstation');
		expect(RESEARCH_CONTEXT_LABEL).toBe('Research only');
		expect(OPERATOR_CHAT_CONTEXT_LABEL).toBe('Operator chat');
		expect(SETTINGS_CONTEXT_LABEL).toBe('Loopback settings');
	});

	it('marks Portfolio only on the dashboard route', () => {
		expect(isWorkstationNavActive('/', '/')).toBe(true);
		expect(isWorkstationNavActive('/', '/trade')).toBe(false);
		expect(isWorkstationNavActive('/', '/strategies')).toBe(false);
		expect(isWorkstationNavActive('/', '/audit')).toBe(false);
		expect(isWorkstationNavActive('/', null)).toBe(false);
	});

	it('marks Trade on the trade route only', () => {
		expect(isWorkstationNavActive('/trade', '/trade')).toBe(true);
		expect(isWorkstationNavActive('/trade', '/')).toBe(false);
		expect(isWorkstationNavActive('/trade', '/strategies')).toBe(false);
	});

	it('marks first-class Research, Deploy, Journals, Chat, and Settings routes', () => {
		expect(isWorkstationNavActive('/research', '/research')).toBe(true);
		expect(isWorkstationNavActive('/research', '/strategies')).toBe(false);
		expect(isWorkstationNavActive('/deploy', '/deploy')).toBe(true);
		expect(isWorkstationNavActive('/journals', '/journals')).toBe(true);
		expect(isWorkstationNavActive('/journals', '/memory')).toBe(false);
		expect(isWorkstationNavActive('/chat', '/chat')).toBe(true);
		expect(isWorkstationNavActive('/settings', '/settings')).toBe(true);
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
		expect(isWorkstationNavActive('/memory', '/memory')).toBe(true);
		expect(isWorkstationNavActive('/memory', '/audit')).toBe(false);
		expect(isWorkstationNavActive('/chat', '/chat')).toBe(true);
		expect(isWorkstationNavActive('/chat', '/memory')).toBe(false);
		expect(isWorkstationNavActive('/settings', '/settings')).toBe(true);
		expect(isWorkstationNavActive('/settings', '/chat')).toBe(false);
	});
});
