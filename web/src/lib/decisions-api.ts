/**
 * Decision journal reads: filters, request paths with cursor paging, the HTTP client with
 * structured `DecisionApiError`s, and fail-closed page validation. Re-exported by `decisions.ts`.
 */
import type {
	BarDecision,
	DecisionOutcome,
	DecisionPage,
	DeploymentDecisionsResponse
} from './decisions-types';

/** Default page size for timelines (the API accepts 1..200, default 50). */
export const DECISION_PAGE_SIZE = 50;

export const DECISION_PAGE_LIMIT_MAX = 200;

// ---------------------------------------------------------------------------
// Filters and requests

export type DecisionFilter = 'all' | 'trades' | 'blocked' | 'no_signal';

export const DECISION_FILTERS: readonly { id: DecisionFilter; label: string }[] = [
	{ id: 'all', label: 'All' },
	{ id: 'trades', label: 'Trades' },
	{ id: 'blocked', label: 'Blocked' },
	{ id: 'no_signal', label: 'No signal' }
];

/** Repeated `outcome` query values for a filter; empty means every outcome. */
export function decisionFilterOutcomes(filter: DecisionFilter): readonly DecisionOutcome[] {
	switch (filter) {
		case 'all':
			return [];
		case 'trades':
			return ['entry_signal', 'exit'];
		case 'blocked':
			return ['entry_blocked'];
		case 'no_signal':
			return ['no_signal'];
	}
}

export type DecisionQuery = {
	/** Page size, clamped to the API's 1..200 bound. */
	limit?: number;
	/** Opaque `next_cursor` of the previous page; omit for the newest page. */
	cursor?: string | null;
	/** Outcomes to keep (sent as repeated `outcome`); empty or omitted keeps all. */
	outcomes?: readonly DecisionOutcome[];
	productId?: string | null;
};

/** The strategy endpoint filters by deployment, not by product. */
export type StrategyDecisionQuery = Omit<DecisionQuery, 'productId'> & {
	/** Narrow the strategy history to one of its deployments. */
	deploymentId?: string | null;
};

function pageLimit(limit: number | undefined): number {
	const requested = limit === undefined ? DECISION_PAGE_SIZE : Math.trunc(limit);
	if (!Number.isFinite(requested)) return DECISION_PAGE_SIZE;
	return Math.min(DECISION_PAGE_LIMIT_MAX, Math.max(1, requested));
}

function decisionParams(
	query: DecisionQuery & { deploymentId?: string | null },
	limit: number
): URLSearchParams {
	const params = new URLSearchParams({ limit: String(limit) });
	if (query.cursor !== undefined && query.cursor !== null) params.set('cursor', query.cursor);
	for (const outcome of query.outcomes ?? []) params.append('outcome', outcome);
	if (query.productId !== undefined && query.productId !== null) {
		params.set('product_id', query.productId);
	}
	if (query.deploymentId !== undefined && query.deploymentId !== null) {
		params.set('deployment_id', query.deploymentId);
	}
	return params;
}

/** `GET` path for one deployment's decisions. */
export function deploymentDecisionsPath(deploymentId: string, query: DecisionQuery = {}): string {
	const params = decisionParams(query, pageLimit(query.limit));
	return `/api/v1/deployments/${encodeURIComponent(deploymentId)}/decisions?${params.toString()}`;
}

/** `GET` path for a strategy's decisions across its deployments. */
export function strategyDecisionsPath(
	strategyId: string,
	query: StrategyDecisionQuery = {}
): string {
	const params = decisionParams(query, pageLimit(query.limit));
	return `/api/v1/strategies/${encodeURIComponent(strategyId)}/decisions?${params.toString()}`;
}

/** A failed decisions read; `status` is the HTTP status (404, 400, 503, …). */
export class DecisionApiError extends Error {
	readonly status: number;

	constructor(status: number, message: string) {
		super(message);
		this.name = 'DecisionApiError';
		this.status = status;
	}
}

function statusFallback(status: number): string {
	switch (status) {
		case 400:
			return 'The decision history request was rejected (stale or invalid cursor). Reload the list.';
		case 404:
			return 'Unknown deployment or strategy.';
		case 503:
			return 'Decision storage is temporarily unavailable.';
		default:
			return `Decision history request failed (HTTP ${status}).`;
	}
}

async function errorDetail(response: Response): Promise<string> {
	const fallback = statusFallback(response.status);
	try {
		const payload: unknown = await response.json();
		if (typeof payload !== 'object' || payload === null || !('detail' in payload)) return fallback;
		const detail: unknown = payload.detail;
		if (typeof detail === 'string' && detail.length > 0) return detail;
		if (
			typeof detail === 'object' &&
			detail !== null &&
			'message' in detail &&
			typeof detail.message === 'string' &&
			detail.message.length > 0
		) {
			return detail.message;
		}
	} catch {
		/* keep the status fallback */
	}
	return fallback;
}

/**
 * Validate one decisions page against the paging contract.
 *
 * Fails closed on protocol violations, like the orders/fills ledger reads: a
 * missing collection, more rows than requested, a server count that
 * contradicts its rows, an unknown storage state, or an empty page that still
 * claims more rows — continuing would silently skip or duplicate history.
 */
export function validateDecisionPage(
	body: {
		decisions?: unknown;
		returned?: unknown;
		next_cursor?: unknown;
		storage?: unknown;
	},
	limit: number
): DecisionPage {
	if (!Array.isArray(body.decisions)) {
		throw new Error('The server response is missing its decisions page.');
	}
	const decisions = body.decisions as BarDecision[];
	if (decisions.length > limit) {
		throw new Error(
			`The server sent ${decisions.length} decisions for a ${limit}-row page; the paging contract is broken.`
		);
	}
	if (typeof body.returned === 'number' && body.returned !== decisions.length) {
		throw new Error(
			`The decision page is inconsistent: the server counted ${body.returned} rows but sent ${decisions.length}.`
		);
	}
	if (body.storage !== 'available' && body.storage !== 'unavailable') {
		throw new Error('The server did not say whether decision storage is available.');
	}
	const nextCursor = typeof body.next_cursor === 'string' ? body.next_cursor : null;
	if (body.next_cursor !== null && nextCursor === null) {
		throw new Error('The decision page carries an unreadable cursor.');
	}
	if (nextCursor !== null && decisions.length === 0) {
		throw new Error('The server sent an empty decision page while claiming more rows exist.');
	}
	return { decisions, nextCursor, storage: body.storage };
}

async function readDecisionPage(path: string, limit: number): Promise<DecisionPage> {
	const response = await fetch(path, { headers: { Accept: 'application/json' } });
	if (!response.ok) {
		throw new DecisionApiError(response.status, await errorDetail(response));
	}
	const body = (await response.json()) as Partial<DeploymentDecisionsResponse>;
	return validateDecisionPage(body, limit);
}

/** One newest-first page of a deployment's journaled decisions. */
export async function fetchDeploymentDecisions(
	deploymentId: string,
	query: DecisionQuery = {}
): Promise<DecisionPage> {
	return readDecisionPage(deploymentDecisionsPath(deploymentId, query), pageLimit(query.limit));
}

/** One newest-first page of a strategy's decisions across its deployments. */
export async function fetchStrategyDecisions(
	strategyId: string,
	query: StrategyDecisionQuery = {}
): Promise<DecisionPage> {
	return readDecisionPage(strategyDecisionsPath(strategyId, query), pageLimit(query.limit));
}
