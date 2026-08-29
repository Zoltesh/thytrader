export type StrategyDraft = {
	strategy_id: string;
	version: number;
	name: string;
	description: string | null;
	status: 'draft' | 'published' | 'archived';
	created_at: string;
	sizing: { risk_fraction: string; min_quote_notional: string; max_quote_notional: string };
	portfolio_limits: { max_strategy_exposure_fraction: string };
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
		throw new Error(body.detail ?? 'The requested research operation is unavailable.');
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
