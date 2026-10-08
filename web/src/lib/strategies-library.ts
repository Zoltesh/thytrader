/**
 * Strategy library helpers: New-strategy templates, the origin view (Mine, Research,
 * All), and deletion outcome text. Re-exported by `strategies.ts`.
 */
import templatesDocument from '$lib/generated/strategy-templates.json';
import type { BulkDeleteItem, StrategyDeletionCounts } from './strategies-types';

/** One New-strategy template from the research template catalog. */
export type StrategyTemplateOption = { id: string; name: string; description: string };

function parseTemplateOptions(raw: unknown): StrategyTemplateOption[] {
	if (raw === null || typeof raw !== 'object' || !('templates' in raw)) {
		throw new Error('Generated strategy template catalog is invalid.');
	}
	const templates = raw.templates;
	if (!Array.isArray(templates)) throw new Error('Generated strategy templates must be a list.');
	return templates.map((item: unknown) => {
		if (item === null || typeof item !== 'object') {
			throw new Error('Generated strategy template must be an object.');
		}
		const { id, name, description } = item as Record<string, unknown>;
		if (typeof id !== 'string' || typeof name !== 'string' || typeof description !== 'string') {
			throw new Error('Generated strategy template needs id, name, and description.');
		}
		return { id, name, description };
	});
}

/** Research templates the library's New-strategy picker offers (generated from Python). */
export const STRATEGY_TEMPLATE_OPTIONS: readonly StrategyTemplateOption[] =
	parseTemplateOptions(templatesDocument);

/**
 * Whose strategies a library view shows (ADR 0098). `research` is anything
 * tagged `claude-research` or `research-*` (agent research runs); `operator`
 * is everything else (the "Mine" view); `all` applies no origin filter.
 */
export type StrategyOrigin = 'operator' | 'research' | 'all';

export const STRATEGY_ORIGIN_OPTIONS: readonly { id: StrategyOrigin; label: string }[] = [
	{ id: 'operator', label: 'Mine' },
	{ id: 'research', label: 'Research' },
	{ id: 'all', label: 'All' }
];

const ORIGIN_STORAGE_KEY = 'thytrader.strategyLibraryOrigin';

/** Whether one tag marks agent research, matching the server's origin filter. */
export function isResearchTag(tag: string): boolean {
	return tag === 'claude-research' || tag.startsWith('research-');
}

/** The viewer's last library view; defaults to Mine. Storage is a convenience only. */
export function readStoredOrigin(): StrategyOrigin {
	try {
		const stored = globalThis.localStorage?.getItem(ORIGIN_STORAGE_KEY);
		return stored === 'research' || stored === 'all' || stored === 'operator' ? stored : 'operator';
	} catch {
		return 'operator';
	}
}

export function rememberOrigin(origin: StrategyOrigin): void {
	try {
		globalThis.localStorage?.setItem(ORIGIN_STORAGE_KEY, origin);
	} catch {
		// Storage is a convenience only.
	}
}

/** Plain list of what deleting one strategy removes, skipping zero counts. */
export function deletionCountsText(counts: StrategyDeletionCounts): string[] {
	const parts: [number, string, string][] = [
		[counts.backtests, 'backtest', 'backtests'],
		[counts.studies, 'study', 'studies'],
		[counts.research_jobs, 'research job', 'research jobs'],
		[counts.paper_deployments, 'paper bot (with its ledger)', 'paper bots (with their ledgers)'],
		[counts.snapshots, 'rules snapshot', 'rules snapshots'],
		[counts.allocations_removed, 'risk-policy allocation', 'risk-policy allocations'],
		[
			counts.portfolio_sleeves ?? 0,
			'portfolio sleeve (journaled in its portfolio)',
			'portfolio sleeves (journaled in their portfolios)'
		]
	];
	const lines = parts
		.filter(([count]) => count > 0)
		.map(([count, one, many]) => `${count} ${count === 1 ? one : many}`);
	if (lines.length === 0) lines.push('No backtests, studies, or bots');
	if (counts.live_deployments_kept > 0) {
		const n = counts.live_deployments_kept;
		lines.push(`${n} stopped live bot${n === 1 ? '' : 's'} kept with full history`);
	}
	return lines;
}

/** One-line outcome for a bulk-delete result row. */
export function bulkOutcomeText(item: BulkDeleteItem): string {
	switch (item.outcome) {
		case 'deleted':
			return 'Deleted';
		case 'would_delete':
			return 'Will be deleted';
		case 'blocked':
			return item.code === 'strategy_has_active_deployments'
				? `Blocked: ${item.deployment_ids.length || 'a'} running or paused bot${item.deployment_ids.length === 1 ? '' : 's'}. Stop ${item.deployment_ids.length === 1 ? 'it' : 'them'} first.`
				: `Blocked: ${item.message ?? 'the server refused this deletion.'}`;
		case 'not_found':
			return 'Not found (already deleted?)';
		case 'failed':
			return `Failed: ${item.message ?? 'no details returned'}`;
	}
}
