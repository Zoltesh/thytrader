import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';

export type DeploymentPosition = {
	product_id: string;
	quantity: string;
	entry_price: string;
	stop_price: string;
	/** Null when the strategy declares no take-profit (ADR 0090). */
	target_price: string | null;
	entered_bar: string;
	side?: 'long' | 'short' | string;
	trail_extreme?: string | null;
	add_count?: number;
	/** Bar whose exit rule matched; the book is exiting until flat (ADR 0093). */
	signal_exit_bar?: string | null;
	protection_status?: string;
	/** Operator reading of this book (ADR 0097); prefer it over the raw phase. */
	position_state?: PositionState | string;
	/** True only while this book's exit is being sent; a resting TP/SL is not an exit. */
	exit_in_flight?: boolean;
	/** Close of the newest bar the bot evaluated for this product (ADR 0098). */
	mark_price?: string | null;
	marked_at?: string | null;
	/** Gross unrealized PnL at `mark_price`, before exit fees; null without a mark. */
	unrealized_pnl?: string | null;
	/** Recorded entry fees allocated to held quantity; null if evidence is unknown. */
	entry_fees?: string | null;
	/** Gross PnL minus entry fees; future exit fees excluded. */
	unrealized_pnl_net?: string | null;
	compatibility_focus?: boolean;
};

/**
 * What a book is doing in operator terms (ADR 0097). The raw `phase` reads
 * `pending_exit` while a TP/SL bracket merely rests; this does not.
 */
export type PositionState =
	'flat' | 'entering' | 'open_protected' | 'open_unprotected' | 'open_unverified' | 'exiting';

/** Plain-language label for a position state, or null when the server sent none. */
export function positionStateLabel(
	state: string | null | undefined,
	options: { hasTarget?: boolean } = {}
): string | null {
	switch (state) {
		case 'flat':
			return 'Flat';
		case 'entering':
			return 'Entry resting';
		case 'open_protected':
			if (options.hasTarget === true) return 'Open · protected (TP/SL resting)';
			if (options.hasTarget === false) return 'Open · protected (stop resting)';
			return 'Open · protected';
		case 'open_unprotected':
			return 'Open · unprotected';
		case 'open_unverified':
			return 'Open · protection unverified';
		case 'exiting':
			return 'Exiting';
		default:
			return null;
	}
}

export type DeploymentInstrumentRuntime = {
	product_id: string;
	phase: string;
	last_evaluated_bar: string | null;
	last_signal: string | null;
	pending_entry_bars: number;
	bars_held: number;
	cooldown_bars_remaining: number;
	pending_stop_price?: string | null;
	pending_target_price?: string | null;
};

export type DeploymentBookTotals = {
	open_books: number;
	working_orders: number;
	fill_count: number;
};

export type DeploymentCapital = {
	allocated_capital?: string | null;
	venue_available_quote?: string | null;
	reserved_buying_power?: string | null;
	inventory_cost?: string | null;
	performance_equity?: string | null;
	initial_equity?: string | null;
	baseline_equity?: string | null;
	high_water_mark_equity?: string | null;
	utc_day_open_equity?: string | null;
};

export type DeploymentOrder = {
	id: string;
	client_order_id: string;
	venue_order_id: string | null;
	product_id?: string;
	side: string;
	kind: string;
	quantity: string;
	price: string | null;
	stop_trigger_price?: string | null;
	take_profit_price?: string | null;
	filled_quantity: string;
	status: string;
	reject_reason: string | null;
	created_at: string;
	updated_at: string;
	attached_child_venue_order_id?: string | null;
	parent_order_id?: string | null;
	pyramid_add?: boolean;
};

export type DeploymentFill = {
	id: string;
	order_id: string;
	product_id?: string;
	venue_fill_id: string;
	price: string;
	quantity: string;
	fee: string;
	filled_at: string;
};

export type Deployment = {
	id: string;
	/** Snapshot of the rules this deployment runs (fixed at start). */
	strategy_fingerprint: string | null;
	/** Owning strategy; null for discretionary books and for kept live books of a deleted strategy. */
	strategy_id: string | null;
	/** Strategy name captured when the deployment started. */
	strategy_name?: string | null;
	/** True for a kept live book whose strategy was deleted. */
	strategy_deleted?: boolean;
	/** The portfolio this bot is a sleeve of (ADR 0091); null for a standalone bot. */
	portfolio_id?: string | null;
	kind: 'strategy' | 'discretionary' | string;
	timeframe: string | null;
	product_id: string;
	mode: 'paper' | 'live';
	status: string;
	/** Raw worker phase; `pending_exit` includes resting TP/SL protection. */
	phase: string;
	/** Worst book's operator state (ADR 0097). */
	position_state?: PositionState | string;
	exit_in_flight?: boolean;
	cash: string;
	paper_starting_cash: string | null;
	maker_fee_rate?: string | null;
	taker_fee_rate?: string | null;
	last_evaluated_bar: string | null;
	last_signal: string | null;
	mismatch_detail: string | null;
	pending_entry_bars: number;
	bars_held: number;
	/** Lifecycle contract: controls stay hidden unless all five arrive valid. */
	lifecycle_command: string;
	daily_loss_latched: boolean;
	drawdown_latched: boolean;
	revision: number;
	worker_lease_held: boolean;
	created_at: string;
	updated_at: string;
	position: DeploymentPosition | null;
	positions?: DeploymentPosition[];
	instrument_runtimes?: DeploymentInstrumentRuntime[];
	book_totals?: DeploymentBookTotals;
	capital?: DeploymentCapital;
	/** Aggregate fill-ledger statistics; null when the summary has not been computed. */
	ledger?: {
		trade_count: number;
		total_net_pnl: string | null;
		total_return_fraction: string | null;
		mark_complete: boolean;
		marked_exposure: string | null;
	} | null;
	orders: DeploymentOrder[];
	fills: DeploymentFill[];
};

const WORKING_ORDER_STATUSES = new Set(['pending', 'open', 'unknown']);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const method = init?.method?.toUpperCase() ?? 'GET';
	if (method !== 'GET' && method !== 'HEAD') {
		await ensureBrowserCsrfSession();
	}
	const response = await fetch(path, {
		...init,
		headers: {
			'content-type': 'application/json',
			...(init?.headers ?? {}),
			...mutationHeaders()
		}
	});
	if (!response.ok) {
		let message = `Request failed (${response.status})`;
		try {
			const body = (await response.json()) as { detail?: unknown };
			if (typeof body.detail === 'string') message = body.detail;
		} catch {
			/* keep fallback */
		}
		throw new Error(message);
	}
	return (await response.json()) as T;
}

export function canonicalPositions(deployment: Deployment): DeploymentPosition[] {
	if (deployment.positions && deployment.positions.length > 0) {
		return [...deployment.positions].sort((left, right) =>
			left.product_id.localeCompare(right.product_id)
		);
	}
	if (deployment.position) {
		return [
			{
				...deployment.position,
				product_id: deployment.position.product_id || deployment.product_id,
				protection_status: deployment.position.protection_status ?? 'unknown',
				compatibility_focus: true
			}
		];
	}
	return [];
}

export function canonicalBooks(deployment: Deployment): DeploymentInstrumentRuntime[] {
	if (deployment.instrument_runtimes && deployment.instrument_runtimes.length > 0) {
		return [...deployment.instrument_runtimes].sort((left, right) =>
			left.product_id.localeCompare(right.product_id)
		);
	}
	return [
		{
			product_id: deployment.product_id,
			phase: deployment.phase,
			last_evaluated_bar: deployment.last_evaluated_bar,
			last_signal: deployment.last_signal,
			pending_entry_bars: deployment.pending_entry_bars,
			bars_held: deployment.bars_held,
			cooldown_bars_remaining: 0
		}
	];
}

export function capitalSummary(deployment: Deployment): string | null {
	const capital = deployment.capital;
	if (!capital) {
		return null;
	}
	const parts: string[] = [];
	if (capital.allocated_capital) {
		parts.push(`allocated ${capital.allocated_capital}`);
	}
	if (deployment.mode === 'live') {
		if (capital.venue_available_quote) {
			parts.push(`venue ${capital.venue_available_quote}`);
		} else {
			parts.push('venue unknown');
		}
	}
	if (capital.performance_equity) {
		parts.push(`equity ${capital.performance_equity}`);
	}
	if (capital.inventory_cost) {
		parts.push(`inventory ${capital.inventory_cost}`);
	}
	return parts.length > 0 ? parts.join(' · ') : null;
}

export function bookTotalsReconcile(deployment: Deployment): boolean {
	const totals = deployment.book_totals;
	if (!totals) return true;
	const working = deployment.orders.filter((order) =>
		WORKING_ORDER_STATUSES.has(order.status)
	).length;
	return (
		totals.open_books === canonicalPositions(deployment).length &&
		totals.working_orders === working &&
		totals.fill_count === deployment.fills.length
	);
}

export function orderProductId(deployment: Deployment, order: DeploymentOrder): string {
	return order.product_id || deployment.product_id;
}

export function fillProductId(deployment: Deployment, fill: DeploymentFill): string {
	return fill.product_id || deployment.product_id;
}

export async function listDeployments(): Promise<Deployment[]> {
	return (await request<{ deployments: Deployment[] }>('/api/v1/deployments')).deployments;
}

export type DeploymentListPage = {
	deployments: Deployment[];
	/** True only when the server returned a full page; an empty page can never claim more. */
	hasMore: boolean;
};

/**
 * Fetch one offset page of the deployment inventory.
 *
 * `hasMore` is `returned === limit`, the only signal the bounded list contract
 * provides; a page shorter than the limit is the end of the inventory.
 */
export async function listDeploymentsPage(
	limit: number,
	offset: number,
	options: { strategyId?: string } = {}
): Promise<DeploymentListPage> {
	const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
	if (options.strategyId !== undefined) params.set('strategy_id', options.strategyId);
	const body = await request<{
		deployments: Deployment[];
		returned?: number;
	}>(`/api/v1/deployments?${params.toString()}`);
	const returned = body.deployments.length;
	if (returned > limit) {
		throw new Error(`Deployment inventory exceeded the requested page limit (${limit}).`);
	}
	// `returned` is present in the real contract; absent only in legacy test
	// doubles. Fail closed when the server's count contradicts its rows.
	if (body.returned !== undefined && body.returned !== returned) {
		throw new Error(
			`Deployment inventory is inconsistent: the server counted ${body.returned} rows but sent ${returned}.`
		);
	}
	return { deployments: body.deployments, hasMore: returned === limit && returned > 0 };
}

/** Backend page ceiling for the bounded deployment inventory read. */
const INVENTORY_PAGE_SIZE = 200;
/** Hard stop so a misbehaving server cannot keep a consumer paging forever. */
const MAX_INVENTORY_PAGES = 25;

/**
 * Fetch the complete deployment inventory, cursor/offset page by page.
 *
 * Selection flows that must never silently truncate (exact-version runtime
 * grouping, other-version discovery) follow `hasMore` until a short page ends
 * the inventory. Fails closed at the page cap instead of presenting a prefix.
 */
export async function listAllDeployments(
	onPage?: (rows: Deployment[]) => void,
	options: { strategyId?: string } = {}
): Promise<Deployment[]> {
	const rows: Deployment[] = [];
	let offset = 0;
	for (let page = 0; page < MAX_INVENTORY_PAGES; page += 1) {
		const result = await listDeploymentsPage(INVENTORY_PAGE_SIZE, offset, options);
		rows.push(...result.deployments);
		onPage?.([...rows]);
		if (!result.hasMore) return rows;
		offset += result.deployments.length;
	}
	throw new Error('Deployment inventory truncated: exceeded the 25-page fetch cap.');
}

export type DeploymentLedgerPage<T> = {
	rows: T[];
	nextCursor: string | null;
};

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

/** One operator performance slice for a runtime deployment. */
export type OperatorPerformance = {
	mode: 'backtest' | 'paper' | 'live' | string;
	timeframe: string;
	/** Quote currency provenance; null means the server could not prove it. */
	currency: 'USD' | 'USDC' | 'USDT' | null;
	strategy_fingerprint: string | null;
	deployment_id: string | null;
	trade_count: number | null;
	total_net_pnl: string | null;
	total_return_fraction: string | null;
	maximum_drawdown_fraction: string | null;
	mark_complete: boolean | null;
	marked_exposure: string | null;
};

/** One operator performance report envelope. */
export type OperatorPerformanceReport = {
	report_kind: string;
	overall_status: string;
	partial_result_warnings: string[];
	recommended_next_action: string;
	payload: OperatorPerformance;
};

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

/** Every deployment of one strategy (`?strategy_id=`), followed across bounded pages. */
export async function listStrategyDeployments(strategyId: string): Promise<Deployment[]> {
	const rows = await listAllDeployments(undefined, { strategyId });
	// Defensive: keep only rows the server says belong to this strategy.
	return rows.filter((deployment) => deployment.strategy_id === strategyId);
}

/** Display name for a deployment's strategy, labelling detached live books honestly. */
export function deploymentStrategyLabel(deployment: Deployment, knownName: string | null): string {
	const name = knownName ?? deployment.strategy_name ?? null;
	if (deployment.strategy_deleted === true) return `${name ?? 'Strategy'} (deleted strategy)`;
	return name ?? 'Strategy';
}

/** Deliberate comparison metadata, independent of deployment lifecycle and orders. */
export type DeploymentTwinLink = {
	paper_deployment_id: string;
	live_deployment_id: string;
	linked_at: string;
};
export type DeploymentTwinResponse = { deployment_id: string; twin: DeploymentTwinLink | null };

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
