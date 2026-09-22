/**
 * Shared workstation chrome: primary nav groups and active-route matching.
 *
 * Nav is grouped by operator verb — watch running state, do research/trading
 * work, govern the system — instead of one flat list of eleven links.
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
	| '/deployments'
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
		| 'Deployments'
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

export type WorkstationNavGroup = {
	/** Visible group label; also the accessible heading for the link set. */
	label: 'Watch' | 'Work' | 'Govern';
	items: readonly WorkstationNavItem[];
};

export const WORKSTATION_NAV_GROUPS: readonly WorkstationNavGroup[] = [
	{
		label: 'Watch',
		items: [
			{ href: '/', label: 'Portfolio' },
			{ href: '/deployments', label: 'Deployments' }
		]
	},
	{
		label: 'Work',
		items: [
			{ href: '/strategies', label: 'Strategies' },
			{ href: '/backtests', label: 'Backtests' },
			{ href: '/research', label: 'Research' },
			{ href: '/deploy', label: 'Deploy' },
			{ href: '/trade', label: 'Trade' }
		]
	},
	{
		label: 'Govern',
		items: [
			{ href: '/journals', label: 'Journals' },
			{ href: '/chat', label: 'Chat' },
			{ href: '/audit', label: 'Audit' },
			{ href: '/memory', label: 'Memory' },
			{ href: '/settings', label: 'Settings' }
		]
	}
];

/** Flat nav in display order; derived once so route matching stays a plain lookup. */
export const WORKSTATION_NAV: readonly WorkstationNavItem[] = WORKSTATION_NAV_GROUPS.flatMap(
	(group) => group.items
);

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
