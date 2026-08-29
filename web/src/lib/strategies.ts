export type StrategyDraft = {
	strategy_id: string;
	version: number;
	name: string;
	description: string | null;
	status: 'draft' | 'published' | 'archived';
	created_at: string;
	sizing: {
		kind?: string;
		risk_fraction: string;
		min_quote_notional: string;
		max_quote_notional: string;
	};
	portfolio_limits: { max_strategy_exposure_fraction: string; max_concurrent_positions?: number };
	[key: string]: unknown;
};

export type StrategyLibraryBacktest = {
	result_fingerprint: string;
	published_at: string;
	summary: {
		initial_equity: string;
		final_equity: string;
		total_return_fraction: string;
		trade_count: number;
		win_rate: string;
		maximum_drawdown_fraction: string;
		[key: string]: unknown;
	};
};

export type StrategyLibraryPaperLive = { paper: string; live: string };

export type StrategyLibraryEntry = {
	strategy_id: string;
	name: string;
	product_id: string;
	timeframe: string;
	latest_version: number | null;
	status: string;
	latest_fingerprint: string | null;
	archived: boolean;
	summary: string;
	backtest: StrategyLibraryBacktest | null;
	paper_live: StrategyLibraryPaperLive;
	created_at: string;
	updated_at: string;
};

export type StrategyCreatedResponse = {
	strategy: StrategyDraft;
	revision: number;
	created: StrategyLibraryEntry;
	siblings: StrategyLibraryEntry[];
};

export type DraftVersionResponse = { strategy: StrategyDraft; revision: number };

export type IndicatorInput = 'close' | 'volume' | ['high', 'low', 'close'];

export type IndicatorDraft = {
	id: string;
	kind: 'ema' | 'sma' | 'rsi' | 'atr' | 'volume_sma';
	input: IndicatorInput;
	parameters: { period: number };
};

export type ComparisonOperatorValue =
	| 'greater_than'
	| 'greater_than_or_equal'
	| 'less_than'
	| 'less_than_or_equal'
	| 'equals'
	| 'crosses_above'
	| 'crosses_below';

export type OperandDraft = { indicator: string } | { literal: string };

export type ConditionDraft =
	| { left: OperandDraft; operator: ComparisonOperatorValue; right: OperandDraft }
	| { all: ConditionDraft[] }
	| { any: ConditionDraft[] }
	| { not: ConditionDraft };

export type BuilderModel = {
	strategy_id: string;
	version: number;
	revision: number;
	name: string;
	description: string;
	status: string;
	created_at: string;
	product_id: string;
	base_currency: string;
	timeframe: string;
	warmup_bars: number;
	indicators: IndicatorDraft[];
	entry: { when: ConditionDraft };
	sizing: { risk_fraction: string; min_quote_notional: string; max_quote_notional: string };
	portfolio_limits: { max_strategy_exposure_fraction: string };
	exits: {
		initial_stop: { kind: string; atr_indicator: string; multiple: string };
		take_profit: { kind: string; multiple: string };
		trailing_stop: { enabled: boolean };
		time_exit: { max_bars_held: number };
	};
	execution: {
		entry_preference: string;
		max_entry_wait_bars: number;
		on_unfilled_entry: string;
	};
	cooldown_bars: number;
	metadata: { tags: string[]; notes: string[] };
};

export type Dataset = {
	product_id: string;
	starts_at: string;
	ends_at: string;
	content_fingerprint: string;
};

export type DraftResponse = { strategy: StrategyDraft; revision: number; summary: string };
type StrategyLibraryResponse = { strategies: StrategyLibraryEntry[] };
type PublishedStrategy = { strategy_fingerprint: string; strategy: StrategyDraft };
type ArchivedStrategy = { strategy_fingerprint: string; archived_at: string | null };
type BacktestSubmission = { run_fingerprint: string; result_fingerprint: string };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await fetch(url, {
		...init,
		headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...init?.headers }
	});
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as { detail?: string };
		const detail = body.detail ?? 'no details returned';
		throw new Error(`The research operation failed (HTTP ${response.status}): ${detail}`);
	}
	return (await response.json()) as T;
}

export async function createDraft(): Promise<StrategyCreatedResponse> {
	return request<StrategyCreatedResponse>('/api/v1/strategies', { method: 'POST' });
}

export async function listStrategies(): Promise<StrategyLibraryEntry[]> {
	return (await request<StrategyLibraryResponse>('/api/v1/strategies')).strategies;
}

export async function clonePublishedStrategy(fingerprint: string): Promise<DraftResponse> {
	return request<DraftResponse>('/api/v1/strategies/clone', {
		method: 'POST',
		body: JSON.stringify({ strategy_fingerprint: fingerprint })
	});
}

export async function importStrategy(definition: unknown): Promise<DraftResponse> {
	return request<DraftResponse>('/api/v1/strategies/import', {
		method: 'POST',
		body: JSON.stringify({ strategy: definition })
	});
}

export async function fetchStrategySource(fingerprint: string): Promise<StrategyDraft> {
	return (
		await request<{ strategy: StrategyDraft }>(
			`/api/v1/strategies/source/${encodeURIComponent(fingerprint)}`
		)
	).strategy;
}

export async function fetchDraftVersion(
	strategyId: string,
	version: number
): Promise<DraftVersionResponse> {
	return request<DraftVersionResponse>(
		`/api/v1/strategies/${encodeURIComponent(strategyId)}/versions/${version}`
	);
}

export function toBuilderModel(strategy: StrategyDraft, revision: number): BuilderModel {
	const entry = strategy.entry as { when: ConditionDraft; cooldown_bars: number };
	const exits = strategy.exits as BuilderModel['exits'];
	return {
		strategy_id: strategy.strategy_id,
		version: strategy.version,
		revision,
		name: strategy.name,
		description: strategy.description ?? '',
		status: strategy.status,
		created_at: strategy.created_at,
		product_id: (strategy.instrument as { product_id: string }).product_id,
		base_currency: (strategy.instrument as { base_currency: string }).base_currency,
		timeframe: strategy.timeframe as string,
		warmup_bars: (strategy.data_requirements as { warmup_bars: number }).warmup_bars,
		indicators: (strategy.indicators as IndicatorDraft[]) ?? [],
		entry: { when: entry.when },
		sizing: {
			risk_fraction: strategy.sizing.risk_fraction,
			min_quote_notional: strategy.sizing.min_quote_notional,
			max_quote_notional: strategy.sizing.max_quote_notional
		},
		portfolio_limits: {
			max_strategy_exposure_fraction: strategy.portfolio_limits.max_strategy_exposure_fraction
		},
		exits,
		execution: strategy.execution as BuilderModel['execution'],
		cooldown_bars: entry.cooldown_bars,
		metadata: strategy.metadata as BuilderModel['metadata']
	};
}

export function fromBuilderModel(model: BuilderModel): StrategyDraft {
	return {
		schema_version: '1.0',
		strategy_id: model.strategy_id,
		version: model.version,
		name: model.name,
		description: model.description.trim().length > 0 ? model.description : null,
		status: 'draft',
		created_at: model.created_at,
		instrument: {
			product_id: model.product_id,
			base_currency: model.base_currency,
			quote_currency: 'USD'
		},
		timeframe: model.timeframe,
		data_requirements: {
			warmup_bars: model.warmup_bars,
			required_fields: ['open', 'high', 'low', 'close', 'volume']
		},
		indicators: model.indicators,
		entry: {
			side: 'long',
			when: model.entry.when,
			cooldown_bars: model.cooldown_bars,
			max_open_positions: 1
		},
		sizing: { kind: 'risk_fraction', ...model.sizing },
		portfolio_limits: { ...model.portfolio_limits, max_concurrent_positions: 1 },
		exits: model.exits,
		execution: model.execution,
		metadata: model.metadata
	};
}

export async function saveDraft(draft: StrategyDraft, revision: number): Promise<DraftResponse> {
	return request<DraftResponse>(
		`/api/v1/strategies/${encodeURIComponent(draft.strategy_id)}/versions/${draft.version}`,
		{ method: 'PUT', body: JSON.stringify({ strategy: draft, revision }) }
	);
}

export async function listDatasets(): Promise<Dataset[]> {
	return (await request<{ datasets: Dataset[] }>('/api/v1/market-data/datasets')).datasets;
}

export async function publishDraft(
	draft: StrategyDraft,
	revision: number
): Promise<PublishedStrategy> {
	return request<PublishedStrategy>(
		`/api/v1/strategies/${encodeURIComponent(draft.strategy_id)}/publish`,
		{
			method: 'POST',
			body: JSON.stringify({ strategy: draft, revision })
		}
	);
}

export async function archivePublishedStrategy(fingerprint: string): Promise<ArchivedStrategy> {
	return request<ArchivedStrategy>(
		`/api/v1/strategies/${encodeURIComponent(fingerprint)}/archive`,
		{ method: 'POST' }
	);
}

export async function submitBacktest(input: {
	strategy_fingerprint: string;
	dataset_fingerprint: string;
	evaluation_start: string;
	evaluation_end: string;
	initial_quote_balance: string;
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
}): Promise<BacktestSubmission> {
	return request<BacktestSubmission>('/api/v1/backtests', {
		method: 'POST',
		body: JSON.stringify(input)
	});
}
