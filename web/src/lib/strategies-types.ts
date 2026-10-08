/** Strategy API payload types (ADR 0082). Re-exported by `strategies.ts`. */
/**
 * One canonical strategy definition (ADR 0082). A strategy is one mutable object:
 * there is no draft/published status and no version number. Backtests, studies,
 * and deployments snapshot the definition and record its `strategy_fingerprint`.
 */
export type StrategyDefinition = {
	strategy_id: string;
	name: string;
	description: string | null;
	created_at: string;
	sizing: {
		kind?: string;
		risk_fraction: string;
		min_quote_notional: string;
		max_quote_notional: string;
	};
	portfolio_limits: { max_strategy_exposure_fraction: string; max_concurrent_positions?: number };
	[key: string]: unknown;
};

/** Raw saved document: any JSON object (it may be an invalid work in progress). */
export type StrategyDocument = { [key: string]: unknown };

export type StrategyValidationIssue = { loc: string; message: string };

/** Advisory finding on a valid document; never blocks a save or a run (ADR 0090). */
export type StrategyValidationWarning = { code: string; loc: string; message: string };

export type StrategyValidation = {
	valid: boolean;
	issues: StrategyValidationIssue[];
	warnings?: StrategyValidationWarning[];
};

/** `GET/PUT /api/v1/strategies/{id}` and the create / clone / import responses. */
export type StrategyRecord = {
	strategy_id: string;
	name: string;
	revision: number;
	created_at: string;
	updated_at: string;
	document: StrategyDocument;
	/** Present only when the saved document is a valid definition. */
	strategy: StrategyDefinition | null;
	validation: StrategyValidation;
	/** Fingerprint the next snapshot gets; null while the definition is invalid. */
	current_fingerprint: string | null;
	summary: string | null;
	product_id: string | null;
	timeframe: string | null;
};

export type StrategyLibraryBacktest = {
	result_fingerprint: string;
	strategy_fingerprint?: string;
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
	product_id: string | null;
	timeframe: string | null;
	revision: number;
	valid: boolean;
	/** The document's `metadata.tags` (ADR 0094); filter the library with one. */
	tags?: string[];
	current_fingerprint: string | null;
	summary: string | null;
	created_at: string;
	updated_at: string;
	backtest: StrategyLibraryBacktest | null;
	paper_live: StrategyLibraryPaperLive;
	active_deployment_count: number;
};

/** What deleting one strategy removes (and the live history it keeps). */
export type StrategyDeletionCounts = {
	snapshots: number;
	backtests: number;
	research_runs: number;
	studies: number;
	research_jobs: number;
	dataset_bindings: number;
	paper_deployments: number;
	live_deployments_kept: number;
	allocations_removed: number;
	/** Portfolio sleeves removed (each journaled in its portfolio; ADR 0088). */
	portfolio_sleeves?: number;
};

export type StrategyDeletionResult = {
	strategy_id: string;
	name: string;
	outcome: 'deleted';
	counts: StrategyDeletionCounts;
	risk_policy_republished: boolean;
};

export type BulkDeleteOutcome = 'deleted' | 'would_delete' | 'blocked' | 'not_found' | 'failed';

export type BulkDeleteItem = {
	strategy_id: string;
	name: string | null;
	outcome: BulkDeleteOutcome;
	code: string | null;
	message: string | null;
	deployment_ids: string[];
	counts: StrategyDeletionCounts | null;
};

export type BulkDeleteResponse = {
	dry_run: boolean;
	results: BulkDeleteItem[];
	would_delete?: number;
	deleted: number;
	blocked: number;
	not_found: number;
	failed: number;
};

/** `GET /api/v1/strategies/snapshots/{fingerprint}`. */
export type StrategySnapshot = {
	strategy_fingerprint: string;
	strategy_id: string | null;
	strategy_name: string | null;
	strategy: StrategyDefinition;
	created_at: string;
	is_current: boolean;
};

export type Dataset = {
	product_id: string;
	timeframe: string;
	starts_at: string;
	ends_at: string;
	content_fingerprint: string;
};

export type BacktestLaunchInput = {
	/** The server snapshots this strategy's current definition. */
	strategy_id: string;
	dataset_fingerprint: string;
	htf_dataset_fingerprint?: string;
	indicator_dataset_fingerprints?: { timeframe: string; dataset_fingerprint: string }[];
	evaluation_start: string;
	evaluation_end: string;
	initial_quote_balance: string;
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	/** Optional constant total bid-ask spread stress (bps); omit for none. */
	spread_bps?: string;
};
