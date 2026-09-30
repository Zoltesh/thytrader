/**
 * Read-only loaders the strategy workspace composes from existing endpoints.
 *
 * Nothing here adds a backend contract: risk policy (`GET /api/v1/risk-policy`),
 * portfolio (`GET /api/v1/portfolio`), the operator runtime report's
 * user-order feed (`GET /api/v1/operator/runtime`), and the snapshot lookup
 * (`GET /api/v1/strategies/snapshots/{fingerprint}`) for resolving which
 * strategy owns a fingerprint.
 */
import type { Portfolio } from './portfolio';
import { fetchStrategySnapshot } from './strategies';
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

export type StrategyOwner =
	| { kind: 'owned'; strategyId: string }
	/** The snapshot exists but its strategy was deleted (a kept live history). */
	| { kind: 'deleted'; strategyName: string | null }
	| { kind: 'unknown' };

/**
 * The strategy that owns snapshot `fingerprint` (old fingerprint deep links).
 *
 * Resolved by the snapshot lookup only; nothing is guessed from names.
 */
export async function resolveStrategyOwner(fingerprint: string): Promise<StrategyOwner> {
	try {
		const snapshot = await fetchStrategySnapshot(fingerprint);
		return snapshot.strategy_id === null
			? { kind: 'deleted', strategyName: snapshot.strategy_name }
			: { kind: 'owned', strategyId: snapshot.strategy_id };
	} catch {
		return { kind: 'unknown' };
	}
}
