import { describe, expect, it } from 'vitest';

import {
	DEFAULT_CONTEXT_LABEL,
	OPERATOR_CHAT_CONTEXT_LABEL,
	RESEARCH_CONTEXT_LABEL,
	SETTINGS_CONTEXT_LABEL,
	WORKSTATION_NAV,
	WORKSTATION_NAV_GROUPS,
	isWorkstationNavActive
} from './workstation-chrome';

describe('workstation chrome', () => {
	it('preserves the primary nav labels in route order', () => {
		expect(WORKSTATION_NAV.map((item) => item.label)).toEqual([
			'Portfolio',
			'Deployments',
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

	it('groups the nav into watch, work, and govern clusters', () => {
		expect(WORKSTATION_NAV_GROUPS.map((group) => group.label)).toEqual(['Watch', 'Work', 'Govern']);
		expect(WORKSTATION_NAV_GROUPS[0].items.map((item) => item.label)).toEqual([
			'Portfolio',
			'Deployments'
		]);
		expect(WORKSTATION_NAV_GROUPS[1].items.map((item) => item.label)).toEqual([
			'Strategies',
			'Backtests',
			'Research',
			'Deploy',
			'Trade'
		]);
		expect(WORKSTATION_NAV_GROUPS[2].items.map((item) => item.label)).toEqual([
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

	it('marks Deployments on its own route only', () => {
		expect(isWorkstationNavActive('/deployments', '/deployments')).toBe(true);
		expect(isWorkstationNavActive('/deployments', '/deploy')).toBe(false);
		expect(isWorkstationNavActive('/deployments', '/')).toBe(false);
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
