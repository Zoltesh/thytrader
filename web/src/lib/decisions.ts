/**
 * Durable per-bar decision journal (`thytrader-bar-decision-v1`).
 *
 * The runtime journals one row per (deployment, product, bar start) for every
 * completed bar of every paper and live strategy bot: what it decided, the
 * server-written one-line reason, the evaluated rule tree with actual values,
 * the risk verdict, and any order action. Two read-only endpoints serve it,
 * newest first with opaque cursor paging:
 *
 * - `GET /api/v1/deployments/{id}/decisions` (one bot)
 * - `GET /api/v1/strategies/{id}/decisions` (every bot of one strategy,
 *   optionally narrowed to one `deployment_id`)
 *
 * Everything else here is a pure presentation helper over those payloads.
 * Decimals stay exact strings: the compact formatter only shortens what is
 * displayed, and every rounded value keeps its raw string for a tooltip.
 */
import type { Deployment } from './deployments';
import { marketLabel } from './deployment-detail';
import type { TradeReasonRecord } from './memory';
import { timeframeMinutes } from './strategy-workspace';
import { formatUtcMinute } from './time';

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
	| 'user_feed_gate'
	| 'catch_up'
	| 'entries_disabled';

export type DecisionExitReason = 'stop' | 'trail' | 'target' | 'time' | 'flatten';

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
};

export type DecisionRule = {
	/** Combined entry rule AND the optional higher-timeframe filter. */
	outcome: RuleOutcome;
	entry: ConditionNode;
	htf_filter: DecisionHtfFilter | null;
	signal: DecisionSignal | null;
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
	target_price: string;
};

/** Why the runtime created an order intent. */
export type IntentPurpose = 'entry' | 'take_profit' | 'stop' | 'time_exit' | 'bracket';

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

/** Default page size for timelines (the API accepts 1..200, default 50). */
export const DECISION_PAGE_SIZE = 50;
export const DECISION_PAGE_LIMIT_MAX = 200;

/**
 * Retention of the journal, stated where operators read the history. The cap
 * counts rows, and a multi-instrument bot writes one row per product per bar,
 * so the note says decisions rather than bars.
 */
export const DECISION_RETENTION_NOTE =
	"A decision is journaled for each completed bar a bot evaluates. The journal keeps each bot's newest 20,000 decisions, up to 180 days; older ones age out.";

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

/** Display text for an intent purpose; `—` when the journal could not name it. */
export function intentPurposeLabel(purpose: IntentPurpose | null): string {
	return purpose === null ? '—' : purpose.replaceAll('_', ' ');
}

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

// ---------------------------------------------------------------------------
// Rows and paging

/** Unique row identity: one row per (deployment, product, bar start). */
export function decisionKey(decision: BarDecision): string {
	return `${decision.deployment_id}|${decision.product_id}|${decision.bar_starts_at}`;
}

/** Append an older page, dropping rows already shown (never renders a bar twice). */
export function appendDecisionPage(
	current: readonly BarDecision[],
	incoming: readonly BarDecision[]
): BarDecision[] {
	const seen = new Set(current.map(decisionKey));
	const merged = [...current];
	for (const decision of incoming) {
		const key = decisionKey(decision);
		if (seen.has(key)) continue;
		seen.add(key);
		merged.push(decision);
	}
	return merged;
}

/** Products named by loaded rows, in first-seen order. */
export function decisionProducts(decisions: readonly BarDecision[]): string[] {
	return [...new Set(decisions.map((decision) => decision.product_id))];
}

/** Every intent one row links: its own `intent_id`, then its orders' intents. */
export function decisionIntentIds(decision: BarDecision): string[] {
	const ids = decision.intent_id === null ? [] : [decision.intent_id];
	for (const order of decision.orders) {
		if (!ids.includes(order.intent_id)) ids.push(order.intent_id);
	}
	return ids;
}

export type TradeReasonAssignment = {
	/** Reasons rendered inside a row, by `decisionKey`. */
	byDecision: Map<string, TradeReasonRecord[]>;
	/** The row (`decisionKey`) that renders each linked intent's reason. */
	ownerByIntent: Map<string, string>;
	/** Reasons no loaded row links, newest first as given. */
	unmatched: TradeReasonRecord[];
};

/**
 * Join persisted trade reasons onto loaded decision rows by `intent_id`.
 *
 * Several rows can link one intent (the bar that created it, then bars that
 * repriced or canceled its order), so each reason gets exactly one owner: the
 * oldest loaded row whose own `intent_id` matches, else the oldest loaded row
 * whose orders carry it. Reasons no loaded row links stay `unmatched`
 * (recorded before the journal existed, discretionary tickets, or bars older
 * than the rows loaded so far). A reason is therefore never rendered twice.
 */
export function assignTradeReasons(
	decisions: readonly BarDecision[],
	records: readonly TradeReasonRecord[]
): TradeReasonAssignment {
	const ownByIntent = new Map<string, string>();
	const orderByIntent = new Map<string, string>();
	// Rows arrive newest first: the last write per intent is the oldest row.
	for (const decision of decisions) {
		const key = decisionKey(decision);
		if (decision.intent_id !== null) ownByIntent.set(decision.intent_id, key);
		for (const order of decision.orders) orderByIntent.set(order.intent_id, key);
	}
	const byDecision = new Map<string, TradeReasonRecord[]>();
	const ownerByIntent = new Map<string, string>();
	const unmatched: TradeReasonRecord[] = [];
	for (const record of records) {
		const owner = ownByIntent.get(record.intent_id) ?? orderByIntent.get(record.intent_id);
		if (owner === undefined) {
			unmatched.push(record);
			continue;
		}
		ownerByIntent.set(record.intent_id, owner);
		byDecision.set(owner, [...(byDecision.get(owner) ?? []), record]);
	}
	return { byDecision, ownerByIntent, unmatched };
}

// ---------------------------------------------------------------------------
// Presentation

export type DecisionTone = 'pos' | 'neg' | 'warn' | 'info' | 'muted';

/** Short chip label for an outcome; unknown future values show as raw text. */
export function decisionOutcomeLabel(outcome: DecisionOutcome): string {
	switch (outcome) {
		case 'entry_signal':
			return 'Entry';
		case 'no_signal':
			return 'No signal';
		case 'holding':
			return 'Holding';
		case 'exit':
			return 'Exit';
		case 'entry_blocked':
			return 'Blocked';
		case 'skipped':
			return 'Skipped';
		case 'error':
			return 'Error';
		default: {
			const unknown: never = outcome;
			return String(unknown).replaceAll('_', ' ');
		}
	}
}

/** Chip tone; the label always carries the meaning too (never color alone). */
export function decisionOutcomeTone(outcome: DecisionOutcome): DecisionTone {
	switch (outcome) {
		case 'entry_signal':
			return 'pos';
		case 'holding':
		case 'exit':
			return 'info';
		case 'entry_blocked':
			return 'warn';
		case 'error':
			return 'neg';
		case 'no_signal':
		case 'skipped':
			return 'muted';
		default: {
			const unknown: never = outcome;
			void unknown;
			return 'muted';
		}
	}
}

export function skipReasonLabel(reason: DecisionSkipReason): string {
	switch (reason) {
		case 'cooldown':
			return 'cooldown after the last trade';
		case 'max_open_positions':
			return 'maximum open positions reached';
		case 'warmup':
			return 'indicator warmup';
		case 'pending_entry':
			return 'an entry order is still working';
		case 'paused':
			return 'bot paused';
		case 'stopped':
			return 'bot stopped';
		case 'data_gap':
			return 'market data gap';
		case 'user_feed_gate':
			return 'user-order feed not connected';
		case 'catch_up':
			return 'catching up on missed bars';
		case 'entries_disabled':
			return 'new entries disabled';
		default: {
			const unknown: never = reason;
			return String(unknown).replaceAll('_', ' ');
		}
	}
}

export function exitReasonLabel(reason: DecisionExitReason): string {
	switch (reason) {
		case 'stop':
			return 'stop loss';
		case 'trail':
			return 'trailing stop';
		case 'target':
			return 'take profit';
		case 'time':
			return 'time exit';
		case 'flatten':
			return 'flatten';
		default: {
			const unknown: never = reason;
			return String(unknown).replaceAll('_', ' ');
		}
	}
}

export function decisionActionLabel(action: DecisionAction): string {
	switch (action) {
		case 'none':
			return 'No order action';
		case 'intent_created':
			return 'Order intent persisted';
		case 'order_submitted':
			return 'Order submitted';
		case 'order_canceled':
			return 'Order canceled';
		case 'repriced':
			return 'Order repriced';
		default: {
			const unknown: never = action;
			return String(unknown).replaceAll('_', ' ');
		}
	}
}

export function ruleOutcomeLabel(outcome: RuleOutcome): string {
	switch (outcome) {
		case 'matched':
			return 'matched';
		case 'not_matched':
			return 'not matched';
		case 'undefined':
			return 'could not be evaluated';
		default: {
			const unknown: never = outcome;
			return String(unknown).replaceAll('_', ' ');
		}
	}
}

/** A rule outcome on the pass / fail / unknown chip scale. */
export function ruleOutcomeResult(outcome: RuleOutcome): ConditionResult {
	return outcome === 'matched' ? 'true' : outcome === 'not_matched' ? 'false' : 'unknown';
}

/** Higher-timeframe filter chip text, e.g. `HTF 4h filter matched ✓`. */
export function htfFilterChipText(filter: DecisionHtfFilter): string {
	return `HTF ${filter.timeframe} filter ${ruleOutcomeLabel(filter.outcome)} ${conditionResultGlyph(ruleOutcomeResult(filter.outcome))}`;
}

/** Pass / fail / unknown glyph; paired with `conditionResultLabel` for screen readers. */
export function conditionResultGlyph(result: ConditionResult): string {
	return result === 'true' ? '✓' : result === 'false' ? '✗' : '?';
}

export function conditionResultLabel(result: ConditionResult): string {
	return result === 'true' ? 'met' : result === 'false' ? 'not met' : 'unknown';
}

export function isCrossover(operator: ComparisonOperator): boolean {
	return operator === 'crosses_above' || operator === 'crosses_below';
}

function operandValueText(value: string | null): string {
	return value === null ? 'n/a' : formatDecimalCompact(value);
}

/**
 * One operand as the rule saw it: an indicator shows its label and value
 * (`RSI(14) 47.21`; a crossover shows previous→current), a literal shows its
 * number. `n/a` marks an undefined value, for example during warmup.
 */
export function conditionOperandText(operand: ConditionOperand, crossover: boolean): string {
	if (operand.kind === 'literal') {
		return formatDecimalCompact(operand.value ?? operand.label);
	}
	const current = operandValueText(operand.value);
	if (!crossover) return `${operand.label} ${current}`;
	return `${operand.label} ${operandValueText(operand.previous_value)}→${current}`;
}

/** Comparison chip text with actual values vs thresholds, e.g. `RSI(14) 47.21 ≥ 50 ✗`. */
export function conditionChipText(node: ComparisonNode): string {
	const crossover = isCrossover(node.operator);
	const left = conditionOperandText(node.left, crossover);
	const right = conditionOperandText(node.right, crossover);
	return `${left} ${node.operator_symbol} ${right} ${conditionResultGlyph(node.result)}`;
}

/** Exact operand values for a chip tooltip (the chip itself shows compact numbers). */
export function conditionChipTitle(node: ComparisonNode): string {
	const exact = (operand: ConditionOperand): string => {
		const value = operand.value ?? 'undefined';
		if (operand.kind === 'literal') return value;
		return operand.previous_value === null
			? `${operand.label} = ${value}`
			: `${operand.label} = ${operand.previous_value} → ${value}`;
	};
	return `${node.label} (${conditionResultLabel(node.result)}): ${exact(node.left)}; ${exact(node.right)}`;
}

/** Group heading text, e.g. `ALL ✗`. */
export function conditionGroupText(node: ConditionGroupNode): string {
	return `${node.node.toUpperCase()} ${conditionResultGlyph(node.result)}`;
}

/** What a group requires, for screen readers and tooltips. */
export function conditionGroupDescription(node: ConditionGroupNode): string {
	const requirement =
		node.node === 'all'
			? 'every condition below must hold'
			: node.node === 'any'
				? 'at least one condition below must hold'
				: 'the condition below must not hold';
	return `${requirement}: ${conditionResultLabel(node.result)}`;
}

/**
 * The bar a row decided, start → close in UTC with its clock:
 * `2026-09-21 18:00 → 20:00 UTC · 2h`; the close keeps its date when the bar
 * crosses midnight (`2026-09-20 00:00 → 2026-09-21 00:00 UTC · 1d`).
 */
export function barSpanText(
	decision: Pick<BarDecision, 'bar_starts_at' | 'bar_closes_at' | 'timeframe'>
): string {
	const start = formatUtcMinute(decision.bar_starts_at).replace(' UTC', '');
	const close = formatUtcMinute(decision.bar_closes_at).replace(' UTC', '');
	const sameDay = start.slice(0, 10) === close.slice(0, 10);
	return `${start} → ${sameDay ? close.slice(11) : close} UTC · ${decision.timeframe}`;
}

export function riskVerdictText(risk: DecisionRisk): string {
	const verdict = risk.decision === 'allow' ? 'Risk allowed' : 'Risk denied';
	const detail = risk.detail.trim();
	return `${verdict} (${risk.reason_code})${detail === '' ? '' : ` · ${detail}`}`;
}

export function positionSnapshotText(position: DecisionPosition): string {
	return `${position.side} ${position.quantity} @ ${position.entry_price} · stop ${position.stop_price} · target ${position.target_price}`;
}

/** Empty-history sentence per filter; the clock is named when known. */
export function decisionEmptyText(filter: DecisionFilter, timeframe: string | null): string {
	switch (filter) {
		case 'all':
			return `No decisions journaled yet. A row is recorded after each completed ${timeframe === null ? '' : `${timeframe} `}bar is evaluated.`;
		case 'trades':
			return 'No entries or exits in the journaled decision history.';
		case 'blocked':
			return 'No blocked entries in the journaled decision history.';
		case 'no_signal':
			return 'No bars without a signal in the journaled decision history.';
	}
}

/** Compact bot label for rows that aggregate several deployments. */
export function decisionBotLabel(
	deployment: Pick<Deployment, 'mode' | 'product_id' | 'created_at'>
): string {
	const mode = deployment.mode === 'live' ? 'LIVE' : 'Paper';
	return `${mode} · ${marketLabel(deployment.product_id)} · since ${deployment.created_at.slice(0, 10)}`;
}

// ---------------------------------------------------------------------------
// Numbers and clocks

const DECIMAL_TEXT = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;
/** Beyond this exponent a value is shown verbatim rather than expanded. */
const MAX_EXPANDED_EXPONENT = 64;

/**
 * Compact display of an exact decimal string (indicator values, closes).
 *
 * Keeps at least two fraction digits of precision and at least four
 * significant digits, rounds half up, then trims trailing zeros:
 * `47.2134` → `47.21`, `7.1234` → `7.123`, `0.000012345` → `0.00001235`,
 * `50.000` → `50`. Exponent forms (`1E-7`) expand. Null renders `—`; a value
 * that is not a decimal (`NaN`) is shown verbatim. Display only — never feed
 * the result back into arithmetic.
 */
export function formatDecimalCompact(value: string | null | undefined): string {
	if (value === null || value === undefined) return '—';
	const raw = value.trim();
	if (raw === '') return '—';
	const match = DECIMAL_TEXT.exec(raw);
	if (match === null) return raw;
	const whole = match[2] ?? '';
	const fraction = match[3] ?? '';
	if (whole === '' && fraction === '') return raw;
	const exponent = match[4] === undefined ? 0 : Number(match[4]);
	if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > MAX_EXPANDED_EXPONENT) return raw;

	// value = digits × 10^-scale, with no leading zeros in `digits`.
	let digits = `${whole}${fraction}`.replace(/^0+/, '');
	let scale = fraction.length - exponent;
	if (digits === '') return '0';
	if (scale < 0) {
		digits = `${digits}${'0'.repeat(-scale)}`;
		scale = 0;
	}

	// Fraction digits for four significant digits: `4 - integerDigits` covers both
	// sides of 1 (below 1, `-integerDigits` is the count of leading fraction zeros).
	const integerDigits = digits.length - scale;
	const keep = Math.max(2, 4 - integerDigits);
	if (scale > keep) {
		const drop = scale - keep;
		const kept = digits.slice(0, digits.length - drop);
		const roundUp = (digits[digits.length - drop] ?? '0') >= '5';
		digits = (BigInt(kept === '' ? '0' : kept) + (roundUp ? 1n : 0n)).toString();
		scale = keep;
	}

	const padded = digits.padStart(scale + 1, '0');
	const integerPart = padded.slice(0, padded.length - scale);
	const fractionPart = padded.slice(padded.length - scale).replace(/0+$/, '');
	const text = fractionPart === '' ? integerPart : `${integerPart}.${fractionPart}`;
	return match[1] === '-' && /[1-9]/.test(text) ? `-${text}` : text;
}

/**
 * When the next bar decision is due: the close of the bar after the last
 * evaluated one. `last_evaluated_bar` is a bar START, so this is
 * `last_evaluated_bar + 2 × timeframe`. Null when the bot has never evaluated
 * a bar or its clock cannot be parsed.
 */
export function nextEvaluationAt(
	deployment: Pick<Deployment, 'last_evaluated_bar' | 'timeframe'>
): Date | null {
	const bar = deployment.last_evaluated_bar;
	const timeframe = deployment.timeframe;
	if (bar === null || bar === undefined || timeframe === null || timeframe === undefined) {
		return null;
	}
	const minutes = timeframeMinutes(timeframe);
	if (minutes === null) return null;
	const startsAt = Date.parse(bar);
	if (Number.isNaN(startsAt)) return null;
	return new Date(startsAt + 2 * minutes * 60_000);
}

/**
 * Header line for when the bot next decides, approximately (workers evaluate
 * shortly after each close). Honest when unknown: before the first evaluation
 * it names the clock; a stopped bot is advanced only while residual exposure
 * remains; a deployment without a parseable clock shows `—`.
 */
export function nextEvaluationText(
	deployment: Pick<Deployment, 'last_evaluated_bar' | 'timeframe' | 'status'>
): string {
	const timeframe = deployment.timeframe;
	if (timeframe === null || timeframe === undefined || timeframeMinutes(timeframe) === null) {
		return 'Next evaluation: — (bar clock unknown)';
	}
	if (deployment.status === 'stopped') {
		return 'Stopped: bars are evaluated only while residual exposure remains';
	}
	const at = nextEvaluationAt(deployment);
	if (at === null) return `Next evaluation ≈ after the next ${timeframe} bar closes`;
	return `Next evaluation ≈ ${formatUtcMinute(at)}`;
}
