/**
 * Shared workstation chrome: primary nav labels and active-route matching.
 *
 * Context pills are static environment labels (not health). Route layouts set
 * `contextLabel` on page data; the root layout renders it.
 */

export const DEFAULT_CONTEXT_LABEL = 'Local workstation';
export const RESEARCH_CONTEXT_LABEL = 'Research only';
export const OPERATOR_CHAT_CONTEXT_LABEL = 'Operator chat';
export const SETTINGS_CONTEXT_LABEL = 'Loopback settings';

export type WorkstationNavHref =
	| '/'
	| '/strategies'
	| '/backtests'
	| '/research'
	| '/deploy'
	| '/trade'
	| '/journals'
	| '/chat'
	| '/audit'
	| '/memory'
	| '/settings';

export type WorkstationNavItem = {
	href: WorkstationNavHref;
	label:
		| 'Portfolio'
		| 'Strategies'
		| 'Backtests'
		| 'Research'
		| 'Deploy'
		| 'Trade'
		| 'Journals'
		| 'Chat'
		| 'Audit'
		| 'Memory'
		| 'Settings';
};

export const WORKSTATION_NAV: readonly WorkstationNavItem[] = [
	{ href: '/', label: 'Portfolio' },
	{ href: '/strategies', label: 'Strategies' },
	{ href: '/backtests', label: 'Backtests' },
	{ href: '/research', label: 'Research' },
	{ href: '/deploy', label: 'Deploy' },
	{ href: '/trade', label: 'Trade' },
	{ href: '/journals', label: 'Journals' },
	{ href: '/chat', label: 'Chat' },
	{ href: '/audit', label: 'Audit' },
	{ href: '/memory', label: 'Memory' },
	{ href: '/settings', label: 'Settings' }
];

/**
 * Whether a primary-nav href is the current SvelteKit route.
 *
 * Portfolio (`/`) is exact-only so it does not stay active on every page.
 * Strategies stays active on `/strategies/[id]` as well as the library.
 */
export function isWorkstationNavActive(href: WorkstationNavHref, routeId: string | null): boolean {
	if (routeId === null) {
		return false;
	}
	if (href === '/') {
		return routeId === '/';
	}
	return routeId === href || routeId.startsWith(`${href}/`);
}
