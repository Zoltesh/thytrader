/**
 * Shared workstation chrome (ADR 0079): the four-destination primary rail,
 * the collapsible System group, breadcrumbs, the Agent panel's "Looking at"
 * line, and the command-palette catalog.
 *
 * Every existing route keeps resolving. Research, Backtests, and Deploy fold
 * under Strategies: since ADR 0080 they are the Test and Run stages of each
 * strategy's workspace (`/strategies/[id]`, `/test`, `/run`, `/why`), and the
 * old routes redirect there or show a small chooser;
 * Deployments is Portfolio; Chat is the Agent side panel, with `/chat` kept as
 * its full-page view for deep links.
 */

export type WorkstationHref =
	| '/'
	| '/strategies'
	| '/research'
	| '/backtests'
	| '/deploy'
	| '/deployments'
	| '/trade'
	| '/settings'
	| '/audit'
	| '/journals'
	| '/memory'
	| '/chat';

export type PrimaryNavItem = {
	id: 'home' | 'strategies' | 'portfolio' | 'trade';
	href: WorkstationHref;
	label: 'Home' | 'Strategies' | 'Portfolio' | 'Trade';
	/** Route-id roots (besides `href`) that keep this destination current. */
	alsoActiveFor: readonly WorkstationHref[];
};

export type SystemNavItem = {
	href: WorkstationHref;
	label: 'Settings' | 'Audit log' | 'Journal' | 'Memory & why-trade';
};

export const PRIMARY_NAV: readonly PrimaryNavItem[] = [
	{ id: 'home', href: '/', label: 'Home', alsoActiveFor: [] },
	{
		id: 'strategies',
		href: '/strategies',
		label: 'Strategies',
		alsoActiveFor: ['/research', '/backtests', '/deploy']
	},
	{ id: 'portfolio', href: '/deployments', label: 'Portfolio', alsoActiveFor: [] },
	{ id: 'trade', href: '/trade', label: 'Trade', alsoActiveFor: [] }
];

export const SYSTEM_NAV: readonly SystemNavItem[] = [
	{ href: '/settings', label: 'Settings' },
	{ href: '/audit', label: 'Audit log' },
	{ href: '/journals', label: 'Journal' },
	{ href: '/memory', label: 'Memory & why-trade' }
];

/** Whether `routeId` is `root` or nested under it. `/` only matches itself. */
function routeUnder(root: WorkstationHref, routeId: string): boolean {
	if (root === '/') {
		return routeId === '/';
	}
	return routeId === root || routeId.startsWith(`${root}/`);
}

/**
 * Whether a nav href is the current SvelteKit route (or one of its children).
 * Home is exact-only so it does not stay active everywhere.
 */
export function isNavHrefActive(href: WorkstationHref, routeId: string | null): boolean {
	return routeId !== null && routeUnder(href, routeId);
}

/** Whether a primary destination is current, including the routes it folds in. */
export function isPrimaryNavActive(item: PrimaryNavItem, routeId: string | null): boolean {
	if (routeId === null) {
		return false;
	}
	return [item.href, ...item.alsoActiveFor].some((root) => routeUnder(root, routeId));
}

/** Whether any System page is current (the group auto-expands then). */
export function isSystemRoute(routeId: string | null): boolean {
	return SYSTEM_NAV.some((item) => isNavHrefActive(item.href, routeId));
}

export type Breadcrumb = {
	/** Parent destination, or null for a top-level page. */
	section: string | null;
	page: string;
};

const BREADCRUMBS: Readonly<Record<string, Breadcrumb>> = {
	'/': { section: null, page: 'Home' },
	'/strategies': { section: 'Strategies', page: 'Library' },
	'/strategies/[id]': { section: 'Strategies', page: 'Build' },
	'/strategies/[id]/test': { section: 'Strategies', page: 'Test' },
	'/strategies/[id]/run': { section: 'Strategies', page: 'Run' },
	'/strategies/[id]/why': { section: 'Strategies', page: 'Why' },
	'/research': { section: 'Strategies', page: 'Research' },
	'/backtests': { section: 'Strategies', page: 'Backtests' },
	'/deploy': { section: 'Strategies', page: 'Deploy' },
	'/deployments': { section: 'Portfolio', page: 'Deployments' },
	'/deployments/[id]': { section: 'Portfolio', page: 'Deployment' },
	'/trade': { section: null, page: 'Trade' },
	'/settings': { section: 'System', page: 'Settings' },
	'/audit': { section: 'System', page: 'Audit log' },
	'/journals': { section: 'System', page: 'Journal' },
	'/memory': { section: 'System', page: 'Memory & why-trade' },
	'/chat': { section: 'Agent', page: 'Operator chat' }
};

export function breadcrumbFor(routeId: string | null): Breadcrumb {
	return (
		(routeId !== null ? BREADCRUMBS[routeId] : undefined) ?? { section: null, page: 'ThyTrader' }
	);
}

/** Short page description for the Agent panel's "Looking at" line. */
export function agentContextLabel(routeId: string | null): string {
	const crumb = breadcrumbFor(routeId);
	return crumb.section === null ? crumb.page : `${crumb.section} · ${crumb.page}`;
}

export type PaletteCommand =
	| {
			kind: 'link';
			id: string;
			group: 'Go to' | 'System' | 'Actions';
			label: string;
			hint?: string;
			href: `/${string}`;
	  }
	| { kind: 'agent'; id: string; group: 'Actions'; label: string; hint?: string };

/**
 * Command-palette catalog: every destination, every System page, the pages
 * folded under a destination, and actions. "New strategy" only navigates to
 * the library's create controls; it never creates a draft by itself.
 */
export const PALETTE_COMMANDS: readonly PaletteCommand[] = [
	...PRIMARY_NAV.map((item): PaletteCommand => ({
		kind: 'link',
		id: `go:${item.id}`,
		group: 'Go to',
		label: item.label,
		href: item.href
	})),
	{
		kind: 'link',
		id: 'go:research',
		group: 'Go to',
		label: 'Research',
		hint: 'Strategies · Test stage',
		href: '/research'
	},
	{
		kind: 'link',
		id: 'go:backtests',
		group: 'Go to',
		label: 'Backtests',
		hint: 'Strategies · all results',
		href: '/backtests'
	},
	{
		kind: 'link',
		id: 'go:deploy',
		group: 'Go to',
		label: 'Deploy',
		hint: 'Strategies · Run stage',
		href: '/deploy'
	},
	{ kind: 'agent', id: 'action:agent', group: 'Actions', label: 'Open agent', hint: 'Side panel' },
	{
		kind: 'link',
		id: 'action:new-strategy',
		group: 'Actions',
		label: 'New strategy',
		hint: 'Strategy library',
		href: '/strategies#library-actions'
	},
	{
		kind: 'link',
		id: 'action:open-strategy',
		group: 'Actions',
		label: 'Open a strategy workspace',
		hint: 'Build · Test · Run · Why',
		href: '/strategies'
	},
	{
		kind: 'link',
		id: 'action:place-order',
		group: 'Actions',
		label: 'Place an order',
		hint: 'Trade',
		href: '/trade'
	},
	{
		kind: 'link',
		id: 'action:chat-page',
		group: 'Actions',
		label: 'Operator chat (full page)',
		hint: 'Agent',
		href: '/chat'
	},
	...SYSTEM_NAV.map((item): PaletteCommand => ({
		kind: 'link',
		id: `system:${item.href.slice(1)}`,
		group: 'System',
		label: item.label,
		href: item.href
	}))
];

/** Case-insensitive substring filter over label, hint, and group; order preserved. */
export function filterPaletteCommands(
	commands: readonly PaletteCommand[],
	query: string
): PaletteCommand[] {
	const needle = query.trim().toLowerCase();
	if (needle === '') {
		return [...commands];
	}
	return commands.filter((command) =>
		[command.label, command.hint ?? '', command.group].some((text) =>
			text.toLowerCase().includes(needle)
		)
	);
}
