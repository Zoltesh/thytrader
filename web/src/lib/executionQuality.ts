/**
 * Read-only execution-quality evidence (`thytrader-execution-quality-v1`, ADR 0116).
 *
 * Recorded fees and causal slippage against completed intent-bar closes. Future
 * closes and unknown counterfactual costs stay null, never rendered as zero.
 */

export const EXECUTION_QUALITY_SCHEMA = 'thytrader-execution-quality-v1';
export const EXECUTION_TWIN_SCHEMA = 'thytrader-execution-twin-comparison-v1';

export type ExecutionQualityFill = {
	fill_id: string;
	order_id: string;
	side: 'buy' | 'sell';
	price: string;
	quantity: string;
	fee: string;
	filled_at: string;
	liquidity: 'maker' | 'taker' | null;
	slippage_bps: string | null;
	reference_price: string | null;
	reference_intent_id: string | null;
	reference_bar_starts_at: string | null;
	reference_bar_closes_at: string | null;
};

export type ExecutionQualityRoundTrip = {
	direction: 'long' | 'short';
	opened_at: string;
	closed_at: string;
	closed_quantity: string;
	entries: ExecutionQualityFill[];
	exits: ExecutionQualityFill[];
	fill_price_pnl_before_fees: string;
	entry_fees: string;
	exit_fees: string;
	net_pnl: string;
	slippage_bps: string | null;
	slippage_fills_journaled: number;
	slippage_fills_total: number;
};

export type ExecutionQualityReport = {
	schema_version: typeof EXECUTION_QUALITY_SCHEMA;
	report_fingerprint: string;
	deployment_id: string;
	product_id: string;
	mode: 'paper' | 'live';
	status: string;
	strategy_fingerprint: string | null;
	timeframe: string | null;
	books: {
		product_id: string;
		closed_trade_count: number;
		round_trips: ExecutionQualityRoundTrip[];
		recorded_fills: ExecutionQualityFill[];
		open_cycle: {
			direction: 'long' | 'short';
			quantity: string;
			entry_fees: string;
			position_matches_ledger: boolean;
			exits: ExecutionQualityFill[];
		} | null;
		fill_price_pnl_before_fees: string;
		entry_fees: string;
		exit_fees: string;
		net_pnl: string;
	}[];
	totals: {
		closed_trade_count: number;
		open_cycle_count: number;
		fill_price_pnl_before_fees: string;
		entry_fees: string;
		exit_fees: string;
		net_pnl: string;
		ledger_realized_delta: string | null;
		weighted_slippage_bps: string | null;
		slippage_fills_journaled: number;
		slippage_fills_total: number;
	};
	evidence: {
		complete: boolean;
		reasons: string[];
		unapplied_fill_count: number;
		orphan_fill_count: number;
	};
};

export type ExecutionTwinComparison = {
	schema_version: typeof EXECUTION_TWIN_SCHEMA;
	comparable: boolean;
	population: 'recorded_fill_lifetime';
	summaries_context_only: boolean;
	reasons: string[];
	paper: { deployment_id: string; net_pnl: string; entry_fees: string; exit_fees: string };
	live: { deployment_id: string; net_pnl: string; entry_fees: string; exit_fees: string };
	fee_normalization: {
		rate_source: 'stored_paper_assumptions' | 'documented_defaults';
		observed_live_fees: string;
		counterfactual_live_fees_at_paper_rates: string | null;
		fee_delta: string | null;
		fills_without_liquidity_evidence: number;
		population: 'live_applied_fill_lifetime';
		fill_count: number;
		complete: boolean;
	} | null;
};

type ApiError = { detail?: { message?: string } };

async function readJson<T>(url: string, fallback: string, signal?: AbortSignal): Promise<T> {
	const response = await fetch(url, { headers: { Accept: 'application/json' }, signal });
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? fallback);
	}
	return (await response.json()) as T;
}

export function fetchExecutionQuality(
	deploymentId: string,
	signal?: AbortSignal
): Promise<ExecutionQualityReport> {
	return readJson(
		`/api/v1/deployments/${encodeURIComponent(deploymentId)}/execution-quality`,
		'Execution-quality evidence is unavailable.',
		signal
	);
}

export function fetchExecutionTwinComparison(
	deploymentId: string,
	signal?: AbortSignal
): Promise<ExecutionTwinComparison> {
	return readJson(
		`/api/v1/deployments/${encodeURIComponent(deploymentId)}/execution-quality/twin`,
		'Execution twin comparison is unavailable.',
		signal
	);
}

const EVIDENCE_REASON_LABELS: Record<string, string> = {
	unapplied_fill_economics: 'A fill is recorded but its economics are not applied yet.',
	fill_without_order: 'A fill has no parent order, so it was excluded.',
	fill_without_journaled_close:
		'The intent has no completed journaled decision close; slippage is unknown.',
	fill_without_intent: 'A fill has no persisted decision intent; no reference price is invented.',
	non_causal_decision_reference:
		'The reference cannot be proven complete before submission and fill; slippage is unknown.',
	liquidity_not_recorded: 'Maker or taker was not recorded for a fill.',
	decision_journal_unavailable: 'The decision journal could not be read.',
	decision_coverage_limited: 'Journal paging stopped before every required intent bar was covered.',
	timeframe_unknown: 'The book has no decision clock, so closes cannot be matched.',
	open_cycle_present: 'A position cycle is still open; its PnL is not closed-trade PnL.',
	open_position_mismatch: 'The open cycle does not match the persisted position row.',
	open_cycle_without_position_row: 'Fills show an open cycle with no position row.',
	position_without_fill_evidence: 'A position row has no recorded fills.',
	position_flip_fill: 'A fill over-covered the cycle and was clamped to the remaining quantity.',
	ledger_realization_delta:
		'Ledger realized PnL differs from closed-cycle totals: partial open-cycle exits, clamping, or fee-allocation rounding can contribute.'
};

export function evidenceReasonLabel(reason: string): string {
	return EVIDENCE_REASON_LABELS[reason] ?? reason;
}

export function liquidityLabel(liquidity: 'maker' | 'taker' | null): string {
	if (liquidity === null) return 'not recorded';
	return liquidity;
}

export function slippageLabel(bps: string | null): string {
	if (bps === null) return 'no causal decision close';
	return `${bps} bps`;
}

export function counterfactualFeeLabel(value: string | null): string {
	return value === null ? 'unavailable (unknown liquidity or incomplete fill coverage)' : value;
}

export function comparableLabel(comparison: ExecutionTwinComparison): string {
	return comparison.comparable ? 'Comparable' : 'Cannot compare';
}
