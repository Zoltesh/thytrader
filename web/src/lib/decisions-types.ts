/** Decision journal (`thytrader-bar-decision-v1`) row and response types. Re-exported by `decisions.ts`. */
export const BAR_DECISION_SCHEMA_VERSION = 'thytrader-bar-decision-v1';

export type DecisionOutcome =
	'entry_signal' | 'no_signal' | 'holding' | 'exit' | 'entry_blocked' | 'skipped' | 'error';

export type DecisionSkipReason =
	| 'cooldown'
	| 'max_open_positions'
	| 'warmup'
	| 'pending_entry'
	| 'paused'
	| 'stopped'
	| 'data_gap'
	| 'bar_settling'
	| 'user_feed_gate'
	| 'catch_up'
	| 'entries_disabled'
	| 'entry_geometry'
	| 'entry_sizing';

/** `signal` is the strategy's `exits.signal_exit` rule (ADR 0093). */
export type DecisionExitReason = 'stop' | 'trail' | 'target' | 'time' | 'flatten' | 'signal';

export type DecisionAction =
	'none' | 'intent_created' | 'order_submitted' | 'order_canceled' | 'repriced';

/** Outcome of one evaluated condition node. */
export type ConditionResult = 'true' | 'false' | 'unknown';

/** Outcome of a whole rule (entry tree, HTF filter, or both combined). */
export type RuleOutcome = 'matched' | 'not_matched' | 'undefined';

export type ComparisonOperator =
	| 'greater_than'
	| 'greater_than_or_equal'
	| 'less_than'
	| 'less_than_or_equal'
	| 'equals'
	| 'crosses_above'
	| 'crosses_below';

/** One side of a comparison with the value the rule actually saw. */
export type ConditionOperand = {
	kind: 'indicator' | 'literal';
	/** Display label such as `RSI(14)` or `50`. */
	label: string;
	/** Indicator key; null for literals. */
	key: string | null;
	/** Exact decimal string; null means undefined (for example during warmup). */
	value: string | null;
	/** Previous bar's value; set only for crossover operands. */
	previous_value: string | null;
};

export type ComparisonNode = {
	node: 'comparison';
	result: ConditionResult;
	/** Server-written rule text such as `RSI(14) ≥ 50`. */
	label: string;
	operator: ComparisonOperator;
	/** `>`, `≥`, `<`, `≤`, `=`, `crosses above`, or `crosses below`. */
	operator_symbol: string;
	left: ConditionOperand;
	right: ConditionOperand;
};

export type ConditionGroupNode = {
	node: 'all' | 'any' | 'not';
	result: ConditionResult;
	children: ConditionNode[];
};

/** Evaluated condition tree, discriminated by `node`. */
export type ConditionNode = ComparisonNode | ConditionGroupNode;

export type DecisionHtfFilter = {
	timeframe: string;
	outcome: RuleOutcome;
	condition: ConditionNode;
};

export type DecisionIndicatorValue = {
	indicator_id: string;
	value: string | null;
};

export type DecisionSignal = {
	candle_starts_at: string;
	indicator_values: DecisionIndicatorValue[];
	entry_condition: RuleOutcome;
	/** Present only for strategies that declare `exits.signal_exit` (ADR 0093). */
	exit_condition?: RuleOutcome | null;
};

export type DecisionRule = {
	/** Combined entry rule AND the optional higher-timeframe filter. */
	outcome: RuleOutcome;
	entry: ConditionNode;
	htf_filter: DecisionHtfFilter | null;
	signal: DecisionSignal | null;
};

/**
 * The evaluated `exits.signal_exit` rule on a bar the book was open (ADR 0093): a
 * holding bar shows why it did not exit, an exit bar the leaves that matched.
 */
export type DecisionExitRule = {
	outcome: RuleOutcome;
	condition: ConditionNode;
};

export type DecisionRisk = {
	decision: 'allow' | 'deny';
	reason_code: string;
	detail: string;
};

export type DecisionPosition = {
	side: 'long' | 'short';
	quantity: string;
	entry_price: string;
	stop_price: string;
	/** Null when the strategy declares no take-profit (ADR 0090). */
	target_price?: string | null;
};

/** Why the runtime created an order intent; `adoption` took over coins already held (ADR 0124). */
export type IntentPurpose =
	'entry' | 'take_profit' | 'stop' | 'time_exit' | 'bracket' | 'signal_exit' | 'adoption';

export type DecisionOrder = {
	order_id: string;
	intent_id: string;
	/** Null when the intent's purpose is not known for this order. */
	purpose: IntentPurpose | null;
	side: 'buy' | 'sell';
	kind: string;
	status: 'pending' | 'open' | 'filled' | 'canceled' | 'rejected' | 'unknown';
	quantity: string;
	price: string | null;
	filled_quantity: string;
	created_at: string;
};

export type DecisionFill = {
	fill_id: string;
	order_id: string;
	purpose: IntentPurpose | null;
	side: 'buy' | 'sell' | null;
	price: string;
	quantity: string;
	fee: string;
	filled_at: string;
};

/** One journaled bar decision (`thytrader-bar-decision-v1`). */
export type BarDecision = {
	protection_update?: {
		kind: 'replacement' | 'canceled_without_replacement';
		canceled_order_ids: string[];
		active_order_ids: string[];
		previous_stop_price: string | null;
		stop_price: string | null;
		target_price: string | null;
		coverage_quantity: string;
		position_quantity: string;
		fully_covered: boolean;
	} | null;
	schema_version: typeof BAR_DECISION_SCHEMA_VERSION;
	deployment_id: string;
	strategy_id: string | null;
	strategy_fingerprint: string | null;
	/** Multi-instrument bots journal one row per covered product per bar. */
	product_id: string;
	timeframe: string;
	mode: 'paper' | 'live';
	bar_starts_at: string;
	bar_closes_at: string;
	evaluated_at: string;
	outcome: DecisionOutcome;
	/** Stable machine code such as `CONDITIONS_NOT_MET` or a risk code. */
	reason_code: string;
	/** Server-written one-line reason. */
	summary: string;
	skip_reason: DecisionSkipReason | null;
	exit_reason: DecisionExitReason | null;
	action: DecisionAction;
	intent_id: string | null;
	order_ids: string[];
	orders: DecisionOrder[];
	fills: DecisionFill[];
	close_price: string | null;
	rule: DecisionRule | null;
	/** Absent on rows written before ADR 0093 and on bars the exit rule was not evaluated. */
	exit_rule?: DecisionExitRule | null;
	risk: DecisionRisk | null;
	position: DecisionPosition | null;
};

/** `unavailable`: the server has no durable journal (no database); not an error. */
export type DecisionStorage = 'available' | 'unavailable';

export type DeploymentDecisionsResponse = {
	deployment_id: string;
	decisions: BarDecision[];
	limit: number;
	returned: number;
	next_cursor: string | null;
	storage: DecisionStorage;
};

export type StrategyDecisionsResponse = {
	strategy_id: string;
	deployment_id: string | null;
	decisions: BarDecision[];
	limit: number;
	returned: number;
	next_cursor: string | null;
	storage: DecisionStorage;
};

/** One validated page of decisions, newest first. */
export type DecisionPage = {
	decisions: BarDecision[];
	nextCursor: string | null;
	storage: DecisionStorage;
};
