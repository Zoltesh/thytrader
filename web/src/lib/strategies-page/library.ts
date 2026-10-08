/**
 * Pure helpers and per-viewer storage of the strategy library page
 * (`routes/strategies/+page.svelte`): the remembered New-strategy market, row
 * dates, per-strategy delete results, and the deletion results headline.
 */
import {
	StrategyApiError,
	formatUtcInputValue,
	strategyErrorCode,
	type BulkDeleteItem,
	type StrategyDeletionResult
} from '$lib/strategies';

const PRODUCT_STORAGE_KEY = 'thytrader.newStrategyProduct';

/** Last market used for New strategy (per viewer); BTC-USDC matches the default USDC policy. */
export function readStoredProduct(): string {
	try {
		return globalThis.localStorage?.getItem(PRODUCT_STORAGE_KEY) || 'BTC-USDC';
	} catch {
		return 'BTC-USDC';
	}
}

export function rememberProduct(productId: string): void {
	try {
		globalThis.localStorage?.setItem(PRODUCT_STORAGE_KEY, productId);
	} catch {
		// Storage is a convenience only.
	}
}

/** A library row's timestamp as `YYYY-MM-DD HH:MM` (UTC). */
export function formatLibraryDate(value: string): string {
	return formatUtcInputValue(new Date(value)).replace('T', ' ');
}

/** The result row of one strategy deleted by the single-strategy endpoint. */
export function deletedItem(strategyId: string, result: StrategyDeletionResult): BulkDeleteItem {
	return {
		strategy_id: strategyId,
		name: result.name,
		outcome: 'deleted',
		code: null,
		message: null,
		deployment_ids: [],
		counts: result.counts
	};
}

/** The result row of a failed single-strategy delete: blocked, not found, or failed. */
export function failedDeleteItem(
	strategyId: string,
	name: string,
	caught: unknown
): BulkDeleteItem {
	const code = strategyErrorCode(caught);
	const detail = caught instanceof StrategyApiError ? caught.detail : {};
	return {
		strategy_id: strategyId,
		name,
		outcome:
			code === 'strategy_has_active_deployments'
				? 'blocked'
				: code === 'strategy_not_found'
					? 'not_found'
					: 'failed',
		code,
		message: caught instanceof Error ? caught.message : null,
		deployment_ids: Array.isArray(detail.deployment_ids)
			? detail.deployment_ids.filter((id): id is string => typeof id === 'string')
			: [],
		counts: null
	};
}

/** Deletion results headline, e.g. `1 deleted · 2 not deleted`. */
export function deleteResultsHeadline(results: readonly BulkDeleteItem[]): string {
	const deleted = results.filter((item) => item.outcome === 'deleted').length;
	const notDeleted = results.filter((item) => item.outcome !== 'deleted').length;
	return `${deleted} deleted${notDeleted > 0 ? ` · ${notDeleted} not deleted` : ''}`;
}
