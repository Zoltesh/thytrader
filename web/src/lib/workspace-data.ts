/**
 * Read-only loaders the strategy workspace composes from existing endpoints.
 *
 * Nothing here adds a backend contract: risk policy (`GET /api/v1/risk-policy`),
 * portfolio (`GET /api/v1/portfolio`), the operator runtime report's
 * user-order feed (`GET /api/v1/operator/runtime`), and strategy source /
 * history for resolving which strategy owns a fingerprint.
 */
import type { Portfolio } from './portfolio';
import { fetchStrategyHistory, fetchStrategySource } from './strategies';
import type { RiskPolicySnapshot, UserOrderFeedState } from './strategy-workspace';

async function readJson<T>(path: string): Promise<T> {
	const response = await fetch(path, { headers: { Accept: 'application/json' } });
	if (!response.ok) throw new Error(`HTTP ${response.status}`);
	return (await response.json()) as T;
}

export async function fetchRiskPolicySnapshot(): Promise<RiskPolicySnapshot> {
	const body = await readJson<RiskPolicySnapshot>('/api/v1/risk-policy');
	return {
		source: body.source,
		version: body.version,
		quote_currency: body.quote_currency,
		allocations: body.allocations ?? []
	};
}

export async function fetchPortfolioSnapshot(): Promise<Portfolio> {
	return readJson<Portfolio>('/api/v1/portfolio');
}

/** The redacted user-order feed state, or null when the report omits it. */
export async function fetchUserOrderFeed(): Promise<{ state: UserOrderFeedState | string } | null> {
	const body = await readJson<{ payload?: { user_order_feed?: { state: string } | null } }>(
		'/api/v1/operator/runtime'
	);
	return body.payload?.user_order_feed ?? null;
}

/**
 * The strategy that publishes `fingerprint`, or null when it cannot be proven.
 *
 * The source record names a `strategy_id`, but only a fingerprint listed in
 * that strategy's published history counts (derived sweep candidates share
 * the id without being versions of it).
 */
export async function resolveStrategyOwner(fingerprint: string): Promise<string | null> {
	try {
		const source = await fetchStrategySource(fingerprint);
		const history = await fetchStrategyHistory(source.strategy_id);
		return history.versions.some((version) => version.strategy_fingerprint === fingerprint)
			? source.strategy_id
			: null;
	} catch {
		return null;
	}
}
