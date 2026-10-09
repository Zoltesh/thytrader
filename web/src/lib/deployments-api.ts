/**
 * Per-deployment HTTP calls: order and fill ledger pages, operator performance, lifecycle
 * commands (start, pause, resume, stop, breaker reset), discretionary orders, and twin
 * links. Re-exported by `deployments.ts`.
 */
import { request } from './deployments-http';
import type {
	Deployment,
	DeploymentFill,
	DeploymentLedgerPage,
	DeploymentOrder,
	DeploymentTwinResponse,
	OperatorPerformanceReport
} from './deployments-types';

/**
 * Fetch one cursor page of a deployment's orders or fills.
 *
 * Fails closed on protocol violations: a page with more rows than the limit,
 * or an empty page that still claims `next_cursor` — either means the paging
 * contract broke and continuing would silently skip or duplicate history.
 */
async function fetchLedgerPage<T>(
	path: string,
	limit: number,
	cursor: string | undefined,
	collectionKey: 'orders' | 'fills'
): Promise<DeploymentLedgerPage<T>> {
	const params = new URLSearchParams({ limit: String(limit) });
	if (cursor !== undefined) params.set('cursor', cursor);
	const body = await request<{
		orders?: T[];
		fills?: T[];
		returned: number;
		next_cursor: string | null;
	}>(`${path}?${params.toString()}`);
	const rows = body[collectionKey];
	if (rows === undefined) {
		throw new Error(`The server response is missing its ${collectionKey} page.`);
	}
	if (rows.length > limit) {
		throw new Error(
			`The server sent ${rows.length} rows for a ${limit}-row page; the paging contract is broken.`
		);
	}
	if (body.next_cursor !== null && rows.length === 0) {
		throw new Error('The server sent an empty page while claiming more rows exist.');
	}
	return { rows, nextCursor: body.next_cursor };
}

export async function listDeploymentOrders(
	id: string,
	limit: number,
	cursor?: string
): Promise<DeploymentLedgerPage<DeploymentOrder>> {
	return fetchLedgerPage<DeploymentOrder>(
		`/api/v1/deployments/${encodeURIComponent(id)}/orders`,
		limit,
		cursor,
		'orders'
	);
}

export async function listDeploymentFills(
	id: string,
	limit: number,
	cursor?: string
): Promise<DeploymentLedgerPage<DeploymentFill>> {
	return fetchLedgerPage<DeploymentFill>(
		`/api/v1/deployments/${encodeURIComponent(id)}/fills`,
		limit,
		cursor,
		'fills'
	);
}

/**
 * Fetch the operator performance report for one deployment.
 *
 * This is the labeled provenance surface (currency, drawdown caveat, mark
 * completeness) — distinct from the local ledger-summary projection.
 */
export async function fetchDeploymentPerformance(id: string): Promise<OperatorPerformanceReport> {
	return request<OperatorPerformanceReport>(
		`/api/v1/operator/performance?deployment_id=${encodeURIComponent(id)}`
	);
}

/**
 * Stop a deployment.
 *
 * Default is managed shutdown (`POST /stop`): cancel risk-increasing entry
 * orders, keep protective exits active. `flatten: true` sends `?flatten=true`
 * to also submit marketable exits for remaining inventory — never inferred
 * from the managed path.
 */
export async function stopDeployment(id: string, flatten = false): Promise<Deployment> {
	const suffix = flatten ? '?flatten=true' : '';
	return request<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}/stop${suffix}`, {
		method: 'POST'
	});
}

/** Clear latched daily-loss and drawdown breakers after explicit operator reset. */
export async function resetBreakerLatches(id: string): Promise<Deployment> {
	return request<Deployment>(
		`/api/v1/deployments/${encodeURIComponent(id)}/reset-breaker-latches`,
		{ method: 'POST' }
	);
}

export async function fetchDeployment(id: string): Promise<Deployment> {
	return request<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}`);
}

/**
 * Start a paper or live deployment of a strategy's current rules (the server snapshots
 * them; the response carries the snapshot `strategy_fingerprint`). Live requires
 * `i_understand_live: true`, which callers set only after the operator accepted the live
 * confirmation (the API answers 428 otherwise).
 */
export async function createDeployment(input: {
	strategy_id: string;
	mode: 'paper' | 'live';
	paper_starting_cash?: string;
	maker_fee_rate?: string;
	taker_fee_rate?: string;
	/** Live only (ADR 0124): start holding this quantity, or "all", of unmanaged coins. */
	adopt_holdings?: string;
	i_understand_live?: boolean;
}): Promise<Deployment> {
	return request<Deployment>('/api/v1/deployments', {
		method: 'POST',
		body: JSON.stringify(input)
	});
}

export async function pauseDeployment(id: string): Promise<Deployment> {
	return request<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}/pause`, {
		method: 'POST'
	});
}

/**
 * Resume a paused deployment. Resuming a live book re-arms live orders, so callers pass
 * `liveAcknowledged: true` only after the operator accepted the live resume confirmation.
 */
export async function resumeDeployment(
	id: string,
	options: { liveAcknowledged?: boolean } = {}
): Promise<Deployment> {
	return request<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}/resume`, {
		method: 'POST',
		...(options.liveAcknowledged ? { body: JSON.stringify({ i_understand_live: true }) } : {})
	});
}

export async function placeDiscretionaryOrder(input: {
	mode: 'paper' | 'live';
	product_id: string;
	stop_price: string;
	take_profit_price: string;
	idempotency_key: string;
	origin: 'human' | 'agent';
	entry_kind?: 'post_only_limit' | 'marketable';
	timeframe?: string;
	side?: 'long' | 'short';
	quantity?: string;
	quote_notional?: string;
	limit_price?: string;
	paper_starting_cash?: string;
	maker_fee_rate?: string;
	taker_fee_rate?: string;
	note?: string;
	/** Required true for live tickets; set only after the live confirmation was accepted. */
	i_understand_live?: boolean;
}): Promise<Deployment> {
	return request<Deployment>('/api/v1/discretionary-orders', {
		method: 'POST',
		body: JSON.stringify(input)
	});
}

export function fetchDeploymentTwin(id: string): Promise<DeploymentTwinResponse> {
	return request(`/api/v1/deployments/${encodeURIComponent(id)}/twin`);
}
export function linkDeploymentTwin(
	id: string,
	counterpartId: string
): Promise<DeploymentTwinResponse> {
	return request(`/api/v1/deployments/${encodeURIComponent(id)}/twin`, {
		method: 'PUT',
		body: JSON.stringify({ counterpart_deployment_id: counterpartId })
	});
}
export function unlinkDeploymentTwin(
	id: string,
	counterpartId: string
): Promise<DeploymentTwinResponse> {
	return request(
		`/api/v1/deployments/${encodeURIComponent(id)}/twin?${new URLSearchParams({ counterpart_deployment_id: counterpartId })}`,
		{ method: 'DELETE' }
	);
}
