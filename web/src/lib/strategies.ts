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

export type StrategyListEntry = {
	strategy: StrategyDraft;
	revision: number | null;
	strategy_fingerprint: string | null;
	archived_at: string | null;
	summary: string;
};

export type Dataset = {
	product_id: string;
	starts_at: string;
	ends_at: string;
	content_fingerprint: string;
};

export type DraftResponse = { strategy: StrategyDraft; revision: number; summary: string };
type StrategyListResponse = { strategies: StrategyListEntry[] };
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

export async function createDraft(): Promise<DraftResponse> {
	return request<DraftResponse>('/api/v1/strategies', { method: 'POST' });
}

export async function listDrafts(): Promise<StrategyListEntry[]> {
	return (await request<StrategyListResponse>('/api/v1/strategies?status=draft')).strategies;
}

export async function listPublishedStrategies(): Promise<StrategyListEntry[]> {
	return (await request<StrategyListResponse>('/api/v1/strategies?status=published')).strategies;
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
