/** Portfolio API payload and request types (ADR 0088, ADR 0091). Re-exported by `portfolios.ts`. */
import type { OpenBook } from './open-books';

export type PortfolioMode = 'paper' | 'live';

export type QuoteCurrency = 'USD' | 'USDC' | 'USDT';

export const QUOTE_CURRENCIES: readonly QuoteCurrency[] = ['USD', 'USDC', 'USDT'];

export type SleeveIssueCode = 'strategy_invalid' | 'quote_currency_mismatch' | 'product_unknown';

export type PortfolioLimits = {
	max_total_exposure_fraction: string;
	max_per_asset_fraction: string;
	daily_loss_quote: string | null;
	max_drawdown_fraction: string | null;
};

export type ManagerPermissions = {
	may_rebalance: boolean;
	max_weight_change_per_week: string;
	may_pause_sleeves: boolean;
	may_propose_sleeves: boolean;
};

export type ManagerSettings = {
	mandate: string;
	permissions: ManagerPermissions;
};

export type PortfolioSleeve = {
	sleeve_id: string;
	strategy_id: string;
	strategy_name: string;
	product_id: string | null;
	covered_product_ids: string[];
	timeframe: string | null;
	quote_currency: string | null;
	strategy_valid: boolean;
	current_fingerprint: string | null;
	weight_fraction: string;
	capital_quote: string;
	note: string | null;
	issues: SleeveIssueCode[];
	created_at: string;
	updated_at: string;
};

export type AssetAllocation = {
	asset: string;
	weight_fraction: string;
	sleeve_ids: string[];
};

export type PortfolioAllocation = {
	allocated_fraction: string;
	cash_reserve_fraction: string;
	unallocated_fraction: string;
	allocated_quote: string;
	cash_reserve_quote: string;
	unallocated_quote: string;
	assets: AssetAllocation[];
	largest_asset: AssetAllocation | null;
	largest_asset_within_limit: boolean | null;
};

export type Portfolio = {
	portfolio_id: string;
	name: string;
	mode: PortfolioMode;
	quote_currency: QuoteCurrency;
	capital_quote: string;
	cash_reserve_fraction: string;
	revision: number;
	created_at: string;
	updated_at: string;
	limits: PortfolioLimits;
	manager: ManagerSettings;
	sleeves: PortfolioSleeve[];
	allocation: PortfolioAllocation;
	/** At least one sleeve and no sleeve issues, so a start can be planned. */
	deployable: boolean;
	deployment_state?: PortfolioDeploymentState;
};

export type PortfolioDeploymentState =
	'not_deployed' | 'running' | 'partially_running' | 'paused' | 'stopped';

/** One sleeve's bot as the portfolio sees it (open it at `/deployments/{id}`). */
export type SleeveDeployment = {
	deployment_id: string;
	strategy_id: string | null;
	strategy_name: string | null;
	status: 'running' | 'paused' | 'stopped' | string;
	/** Raw worker phase; `pending_exit` includes resting TP/SL protection. */
	phase: string;
	/** Operator reading of the sleeve's books (ADR 0097); null when not read. */
	position_state?: string | null;
	exit_in_flight?: boolean | null;
	lifecycle_command: string;
	mismatch_detail: string | null;
	allocated_capital: string | null;
	paper_starting_cash: string | null;
	performance_equity: string | null;
	accounting_complete: boolean;
	net_pnl: string | null;
	return_fraction: string | null;
	drawdown_fraction: string | null;
	exposure_quote: string | null;
	open_books: number | null;
	/** Each open book with entry, stop, target, state, and last-bar PnL (ADR 0098). */
	books?: OpenBook[];
	strategy_fingerprint: string | null;
	running_current_rules: boolean | null;
	created_at: string;
	updated_at: string;
};

export type SleeveBook = {
	sleeve_id: string;
	strategy_id: string;
	strategy_name: string;
	product_id: string | null;
	timeframe: string | null;
	weight_fraction: string;
	target_capital_quote: string;
	issues: SleeveIssueCode[];
	deployment: SleeveDeployment | null;
};

export type PortfolioBreaker = {
	latched: boolean;
	reason_code: 'PORTFOLIO_DAILY_LOSS_STOP' | 'PORTFOLIO_DRAWDOWN_STOP' | string | null;
	detail: string | null;
	latched_at: string | null;
	daily_loss_quote: string | null;
	max_drawdown_fraction: string | null;
	run_started_at: string | null;
	accounting_complete: boolean;
	unresolved_deployment_ids: string[];
	equity: string | null;
	day_open_equity: string | null;
	daily_pnl: string | null;
	high_water_mark_equity: string | null;
	drawdown_fraction: string | null;
	evaluated_at: string | null;
};

export type AssetExposure = {
	asset: string;
	exposure_quote: string | null;
	fraction_of_capital: string | null;
	cap_quote: string;
};

export type PortfolioExposure = {
	accounting_complete: boolean;
	unresolved_deployment_ids: string[];
	total_quote: string | null;
	fraction_of_capital: string | null;
	cap_quote: string;
	asset_cap_quote: string;
	assets: AssetExposure[];
};

export type PortfolioDeployment = {
	portfolio_id: string;
	name: string;
	mode: PortfolioMode;
	quote_currency: QuoteCurrency;
	capital_quote: string;
	revision: number;
	state: PortfolioDeploymentState;
	sleeves: SleeveBook[];
	detached: SleeveDeployment[];
	breaker: PortfolioBreaker;
	exposure: PortfolioExposure;
	pending_proposals: number;
};

export type SleeveOutcomeKind =
	'started' | 'attached' | 'paused' | 'resumed' | 'stopped' | 'unchanged' | 'failed';

export type SleeveOutcome = {
	sleeve_id: string | null;
	strategy_id: string | null;
	strategy_name: string;
	outcome: SleeveOutcomeKind;
	deployment_id: string | null;
	message: string | null;
};

export type PortfolioActionResponse = {
	action: 'start' | 'pause' | 'resume' | 'stop';
	outcomes: SleeveOutcome[];
	deployment: PortfolioDeployment;
};

export type ProposalKind = 'rebalance' | 'pause_sleeve' | 'resume_sleeve' | 'add_sleeve';

export type ProposalStatus = 'pending' | 'applied' | 'declined' | 'failed' | 'expired';

export type ProposalChange =
	| {
			kind: 'rebalance';
			weights: { sleeve_id: string; weight_fraction: string }[];
			cash_reserve_fraction?: string | null;
	  }
	| { kind: 'pause_sleeve'; sleeve_id: string }
	| { kind: 'resume_sleeve'; sleeve_id: string }
	| { kind: 'add_sleeve'; strategy_id: string; weight_fraction: string; note?: string | null };

export type ProposalEvidence = {
	kind: 'backtest_result' | 'portfolio_backtest' | 'study' | 'decision' | 'deployment' | string;
	ref: string;
	note?: string | null;
};

export type Proposal = {
	proposal_id: string;
	portfolio_id: string;
	kind: ProposalKind;
	status: ProposalStatus;
	summary: string;
	rationale: string;
	change: ProposalChange;
	evidence: ProposalEvidence[];
	base_revision: number;
	submitted_by: 'manager' | 'operator';
	channel: string;
	approval_reason?: string | null;
	weight_moved?: string | null;
	created_at: string;
	expires_at: string;
	decided_at?: string | null;
	decided_by?: 'operator' | 'manager' | 'system' | null;
	decision_note?: string | null;
	auto_applied?: boolean;
	applied_revision?: number | null;
	failure_code?: string | null;
	failure_message?: string | null;
};

export type ProposalListResponse = {
	proposals: Proposal[];
	limit: number;
	returned: number;
	total: number;
	has_more: boolean;
	next_cursor: string | null;
};

export type ProposalResponse = { proposal: Proposal; portfolio_revision: number };

/** What the portfolio action dialog confirms. */
export type PortfolioDialogAction = 'start' | 'pause' | 'resume' | 'stop' | 'reset';

export type PortfolioListResponse = {
	portfolios: Portfolio[];
	limit: number;
	returned: number;
	total: number;
	has_more: boolean;
	next_cursor: string | null;
};

export type JournalKind =
	| 'created'
	| 'settings_changed'
	| 'sleeve_added'
	| 'sleeve_updated'
	| 'sleeve_removed'
	| 'weights_changed'
	| 'limits_changed'
	| 'manager_changed'
	| 'backtest_run'
	| 'deployment_started'
	| 'deployment_paused'
	| 'deployment_resumed'
	| 'deployment_stopped'
	| 'breaker_tripped'
	| 'breaker_reset'
	| 'proposal_submitted'
	| 'proposal_approved'
	| 'proposal_declined'
	| 'proposal_failed';

export type JournalChange = { field: string; before: string | null; after: string | null };

export type JournalDetail = {
	sleeve_id?: string | null;
	strategy_id?: string | null;
	strategy_name?: string | null;
	reason?: 'operator' | 'strategy_deleted' | 'manager_proposal' | 'breaker' | null;
	changes?: JournalChange[];
	job_id?: string | null;
	result_fingerprint?: string | null;
	deployment_ids?: string[];
	reason_code?: string | null;
	proposal_id?: string | null;
	rationale?: string | null;
	note?: string | null;
};

export type JournalEntry = {
	entry_id: string;
	portfolio_id: string;
	occurred_at: string;
	kind: JournalKind | string;
	actor: 'operator' | 'system' | 'manager' | string;
	channel: 'browser' | 'api' | 'system' | string;
	summary: string;
	detail: JournalDetail;
	revision: number;
};

export type JournalPage = {
	entries: JournalEntry[];
	limit: number;
	returned: number;
	total: number;
	has_more: boolean;
	next_cursor: string | null;
};

export type BacktestJobStatus =
	'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'expired';

export type PortfolioBacktestJob = {
	job_id: string;
	portfolio_id: string;
	portfolio_revision: number;
	status: BacktestJobStatus;
	created_at: string;
	updated_at: string;
	expires_at: string;
	evaluation_start: string;
	evaluation_end: string;
	sleeve_count: number;
	progress_current: number;
	progress_total: number;
	error_message: string | null;
	failed_detail: string | null;
	result_fingerprint: string | null;
};

export type PlannedSleeve = {
	sleeve_id: string;
	strategy_id: string;
	strategy_fingerprint: string;
	capital_quote: string;
	dataset_fingerprint: string;
};

export type PortfolioBacktestAccepted = {
	job: PortfolioBacktestJob;
	sleeves: PlannedSleeve[];
};

export type PortfolioBacktestListing = {
	result_fingerprint: string;
	portfolio_id: string;
	portfolio_revision: number;
	published_at: string;
	evaluation_start: string;
	evaluation_end: string;
	sleeve_count: number;
	total_return_fraction: string;
	maximum_drawdown_fraction: string;
	idle_capital_fraction: string;
	basket_total_return_fraction: string;
	trade_count: number;
};

export type PortfolioBacktestListResponse = {
	entries: PortfolioBacktestListing[];
	limit: number;
	offset: number;
	returned: number;
	has_more: boolean;
	next_cursor: string | null;
};

export type PortfolioBacktestCosts = {
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	spread_bps: string;
};

export type PortfolioSleeveResult = {
	sleeve_id: string;
	strategy_id: string;
	strategy_name: string;
	strategy_fingerprint: string;
	product_id: string;
	covered_product_ids: string[];
	timeframe: string;
	weight_fraction: string;
	capital_quote: string;
	run_fingerprint: string;
	result_fingerprint: string;
	dataset_fingerprint: string;
	final_equity: string;
	total_return_fraction: string;
	maximum_drawdown_fraction: string;
	trade_count: number;
	win_rate: string;
	contribution_fraction: string;
	correlation_to_rest: string | null;
	long_fraction: string | null;
};

export type PortfolioBacktestSummary = {
	initial_equity: string;
	final_equity: string;
	total_net_pnl: string;
	total_return_fraction: string;
	maximum_drawdown: string;
	maximum_drawdown_fraction: string;
	idle_capital_fraction: string;
	allocated_fraction: string;
	cash_quote: string;
	trade_count: number;
	best_sleeve_return_fraction: string;
	best_sleeve_maximum_drawdown_fraction: string;
	grid_points: number;
};

export type PortfolioCurveMetrics = {
	risk_free_rate: string;
	annualization: string;
	bar_seconds: string | null;
	bars_per_year: string | null;
	sharpe: string | null;
	sortino: string | null;
	calmar: string | null;
	cagr: string | null;
	annualized_volatility: string | null;
};

export type CorrelationPair = { sleeve_ids: [string, string]; coefficient: string | null };

export type PortfolioCorrelation = {
	return_clock_seconds: number;
	observations: number;
	pairs: CorrelationPair[];
};

export type OverlapPair = { sleeve_ids: [string, string]; asset: string; fraction: string };

export type PortfolioOverlap = {
	same_asset_fraction: string;
	long_together_fraction: string;
	pairs: OverlapPair[];
	excluded_sleeve_ids: string[];
};

export type BasketLeg = {
	product_id: string;
	dataset_fingerprint: string;
	timeframe: string;
	entry_price: string;
	exit_price: string;
	quantity: string;
	return_fraction: string;
};

export type PortfolioBasket = {
	legs: BasketLeg[];
	invested_quote: string;
	cash_quote: string;
	final_equity: string;
	total_return_fraction: string;
	maximum_drawdown_fraction: string;
	total_fees: string;
};

export type PortfolioEquityPoint = { at: string; equity: string; basket_equity: string };

export type PortfolioBacktestResult = {
	schema_version: string;
	contract: string;
	engine: string;
	portfolio_id: string;
	portfolio_revision: number;
	portfolio_name: string;
	mode: PortfolioMode;
	quote_currency: QuoteCurrency;
	capital_quote: string;
	cash_reserve_fraction: string;
	evaluation_start: string;
	evaluation_end: string;
	costs: PortfolioBacktestCosts;
	sleeves: PortfolioSleeveResult[];
	summary: PortfolioBacktestSummary;
	metrics: PortfolioCurveMetrics;
	correlation: PortfolioCorrelation;
	overlap: PortfolioOverlap;
	basket: PortfolioBasket;
	equity_curve: PortfolioEquityPoint[];
	disclosures: string[];
};

export type PortfolioBacktestDetail = {
	result_fingerprint: string;
	result: PortfolioBacktestResult;
	equity_curve_points: number;
	equity_curve_downsampled: boolean;
};

export type BacktestProblem = {
	code: string;
	message: string;
	sleeve_id: string | null;
	strategy_id: string | null;
	strategy_name: string | null;
};

export type PortfolioCreateInput = {
	name: string;
	mode: PortfolioMode;
	quote_currency: QuoteCurrency;
	capital_quote: string;
	cash_reserve_fraction: string;
};

export type PortfolioUpdateInput = {
	revision: number;
	name?: string;
	capital_quote?: string;
	cash_reserve_fraction?: string;
	limits?: PortfolioLimits;
	manager?: ManagerSettings;
};

export type SleeveAddInput = {
	revision: number;
	strategy_id: string;
	weight_fraction: string;
	note?: string;
};

export type SetWeightsInput = {
	revision: number;
	weights: { sleeve_id: string; weight_fraction: string }[];
	cash_reserve_fraction?: string;
};

export type BacktestRunInput = {
	revision: number;
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	spread_bps?: string;
};
