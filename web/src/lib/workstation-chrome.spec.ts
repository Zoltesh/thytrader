import { describe, expect, it } from 'vitest';

import {
	PALETTE_COMMANDS,
	PRIMARY_NAV,
	SYSTEM_NAV,
	agentContextLabel,
	breadcrumbFor,
	filterPaletteCommands,
	isNavHrefActive,
	isPrimaryNavActive,
	isSystemRoute,
	type PrimaryNavItem
} from './workstation-chrome';

function primary(id: PrimaryNavItem['id']): PrimaryNavItem {
	const item = PRIMARY_NAV.find((entry) => entry.id === id);
	if (item === undefined) throw new Error(`missing ${id}`);
	return item;
}

describe('workstation chrome', () => {
	it('has exactly four primary destinations in rail order', () => {
		expect(PRIMARY_NAV.map((item) => [item.label, item.href])).toEqual([
			['Home', '/'],
			['Strategies', '/strategies'],
			['Portfolio', '/deployments'],
			['Trade', '/trade']
		]);
	});

	it('keeps rarely used pages in the System group, and Chat out of the rail', () => {
		expect(SYSTEM_NAV.map((item) => [item.label, item.href])).toEqual([
			['Settings', '/settings'],
			['Audit log', '/audit'],
			['Journal', '/journals'],
			['Memory & why-trade', '/memory']
		]);
		const railHrefs = [...PRIMARY_NAV, ...SYSTEM_NAV].map((item) => item.href);
		expect(railHrefs).not.toContain('/chat');
	});

	it('marks Home only on the dashboard route', () => {
		const home = primary('home');
		expect(isPrimaryNavActive(home, '/')).toBe(true);
		expect(isPrimaryNavActive(home, '/trade')).toBe(false);
		expect(isPrimaryNavActive(home, '/strategies')).toBe(false);
		expect(isPrimaryNavActive(home, null)).toBe(false);
	});

	it('keeps Strategies active for the library, builder, research, backtests, and deploy', () => {
		const strategies = primary('strategies');
		for (const route of [
			'/strategies',
			'/strategies/[id]',
			'/strategies/[id]/test',
			'/strategies/[id]/run',
			'/strategies/[id]/why',
			'/research',
			'/backtests',
			'/deploy'
		]) {
			expect(isPrimaryNavActive(strategies, route)).toBe(true);
		}
		expect(isPrimaryNavActive(strategies, '/deployments')).toBe(false);
		expect(isPrimaryNavActive(strategies, '/')).toBe(false);
	});

	it('marks Portfolio on deployments and their detail, not on deploy', () => {
		const portfolio = primary('portfolio');
		expect(isPrimaryNavActive(portfolio, '/deployments')).toBe(true);
		expect(isPrimaryNavActive(portfolio, '/deployments/[id]')).toBe(true);
		expect(isPrimaryNavActive(portfolio, '/deploy')).toBe(false);
		expect(isPrimaryNavActive(portfolio, '/')).toBe(false);
	});

	it('marks Trade on the trade route only', () => {
		const trade = primary('trade');
		expect(isPrimaryNavActive(trade, '/trade')).toBe(true);
		expect(isPrimaryNavActive(trade, '/strategies')).toBe(false);
	});

	it('matches System pages exactly and detects System routes', () => {
		expect(isNavHrefActive('/settings', '/settings')).toBe(true);
		expect(isNavHrefActive('/journals', '/memory')).toBe(false);
		expect(isNavHrefActive('/audit', '/backtests')).toBe(false);
		expect(isSystemRoute('/memory')).toBe(true);
		expect(isSystemRoute('/audit')).toBe(true);
		expect(isSystemRoute('/strategies')).toBe(false);
		expect(isSystemRoute('/chat')).toBe(false);
		expect(isSystemRoute(null)).toBe(false);
	});

	it('builds section / page breadcrumbs for every route', () => {
		expect(breadcrumbFor('/')).toEqual({ section: null, page: 'Home' });
		expect(breadcrumbFor('/strategies/[id]')).toEqual({ section: 'Strategies', page: 'Build' });
		expect(breadcrumbFor('/strategies/[id]/test')).toEqual({ section: 'Strategies', page: 'Test' });
		expect(breadcrumbFor('/strategies/[id]/run')).toEqual({ section: 'Strategies', page: 'Run' });
		expect(breadcrumbFor('/strategies/[id]/why')).toEqual({ section: 'Strategies', page: 'Why' });
		expect(breadcrumbFor('/research')).toEqual({ section: 'Strategies', page: 'Research' });
		expect(breadcrumbFor('/deployments')).toEqual({ section: null, page: 'Portfolio' });
		expect(breadcrumbFor('/deployments/[id]')).toEqual({ section: 'Portfolio', page: 'Bot' });
		expect(breadcrumbFor('/settings')).toEqual({ section: 'System', page: 'Settings' });
		expect(breadcrumbFor('/chat')).toEqual({ section: 'Agent', page: 'Operator chat' });
		expect(breadcrumbFor(null)).toEqual({ section: null, page: 'ThyTrader' });
	});

	it('describes the current page for the agent panel', () => {
		expect(agentContextLabel('/')).toBe('Home');
		expect(agentContextLabel('/deploy')).toBe('Strategies · Deploy');
	});

	it('lists every destination, System page, and required action in the palette', () => {
		const labels = PALETTE_COMMANDS.map((command) => command.label);
		for (const item of [...PRIMARY_NAV, ...SYSTEM_NAV]) {
			expect(labels).toContain(item.label);
		}
		expect(labels).toEqual(
			expect.arrayContaining(['Research', 'Backtests', 'Deploy', 'Open agent', 'New strategy'])
		);
		const agent = PALETTE_COMMANDS.find((command) => command.label === 'Open agent');
		expect(agent?.kind).toBe('agent');
		const create = PALETTE_COMMANDS.find((command) => command.label === 'New strategy');
		expect(create).toMatchObject({ kind: 'link', href: '/strategies#library-actions' });
	});

	it('filters palette commands by label, hint, or group', () => {
		expect(filterPaletteCommands(PALETTE_COMMANDS, '')).toHaveLength(PALETTE_COMMANDS.length);
		expect(filterPaletteCommands(PALETTE_COMMANDS, 'AUDIT').map((c) => c.label)).toEqual([
			'Audit log'
		]);
		expect(
			filterPaletteCommands(PALETTE_COMMANDS, 'system').every(
				(command) => command.group === 'System'
			)
		).toBe(true);
		expect(filterPaletteCommands(PALETTE_COMMANDS, 'zzz-no-match')).toEqual([]);
	});
});
