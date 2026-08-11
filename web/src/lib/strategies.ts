export type StrategyDraft = {
	strategy_id: string;
	name: string;
	description: string | null;
	status: 'draft' | 'published' | 'archived';
	sizing: { risk_fraction: string; min_quote_notional: string; max_quote_notional: string };
	portfolio_limits: { max_strategy_exposure_fraction: string };
	[key: string]: unknown;
};

type DraftResponse = { strategy: StrategyDraft };
type PublishedStrategy = { strategy_fingerprint: string; strategy: StrategyDraft };
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

export async function createDraft(): Promise<StrategyDraft> {
	return (await request<DraftResponse>('/api/v1/strategies', { method: 'POST' })).strategy;
}

export async function publishDraft(draft: StrategyDraft): Promise<PublishedStrategy> {
	return request<PublishedStrategy>(
		`/api/v1/strategies/${encodeURIComponent(draft.strategy_id)}/publish`,
		{
			method: 'POST',
			body: JSON.stringify({ strategy: draft })
		}
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
