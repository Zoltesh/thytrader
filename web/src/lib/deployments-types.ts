/**
 * Deployment API payload types: the deployment with its books, orders, fills and capital,
 * inventory and ledger pages, operator performance, and twin links. Re-exported by
 * `deployments.ts`.
 */
import type { PositionState } from '$lib/position-state';
import type { ProtectionEvidence } from '$lib/protection-evidence';

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
	/** Quantitative stop cover (ADR 0112). Absent only on older payloads. */
	protection?: ProtectionEvidence | null;
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
	performance_capital_quote?: string | null;
	performance_maximum_drawdown_fraction?: string | null;
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

export type DeploymentListPage = {
	deployments: Deployment[];
	/** Server `has_more`. A full page is not evidence of another page. */
	hasMore: boolean;
	/** Pin this on the next offset page so inserts cannot shift the snapshot. */
	asOf: string | null;
	total: number | null;
	order: string | null;
	fingerprint: string | null;
	nextCursor: string | null;
};

export type DeploymentLedgerPage<T> = {
	rows: T[];
	nextCursor: string | null;
};

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

/** Deliberate comparison metadata, independent of deployment lifecycle and orders. */
export type DeploymentTwinLink = {
	paper_deployment_id: string;
	live_deployment_id: string;
	linked_at: string;
};
export type DeploymentTwinResponse = { deployment_id: string; twin: DeploymentTwinLink | null };
