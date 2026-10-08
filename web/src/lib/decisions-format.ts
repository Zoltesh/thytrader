/** Decision presentation: retention copy, labels, tones, and chip/row text. Re-exported by `decisions.ts`. */
import type { Deployment } from './deployments';
import { marketLabel } from './deployment-detail';
import { formatUtcMinute } from './time';
import type {
	BarDecision,
	ComparisonNode,
	ComparisonOperator,
	ConditionGroupNode,
	ConditionOperand,
	ConditionResult,
	DecisionAction,
	DecisionExitReason,
	DecisionHtfFilter,
	DecisionOutcome,
	DecisionPosition,
	DecisionRisk,
	DecisionSkipReason,
	IntentPurpose,
	RuleOutcome
} from './decisions-types';
import type { DecisionFilter } from './decisions-api';
import { formatDecimalCompact } from './decisions-numbers';

/**
 * Retention of the journal, stated where operators read the history. The cap
 * counts rows, and a multi-instrument bot writes one row per product per bar,
 * so the note says decisions rather than bars.
 */
export const DECISION_RETENTION_NOTE =
	"A decision is journaled for each completed bar a bot evaluates. The journal keeps each bot's newest 20,000 decisions, up to 180 days; older ones age out.";

/** Display text for an intent purpose; `—` when the journal could not name it. */
export function intentPurposeLabel(purpose: IntentPurpose | null): string {
	return purpose === null ? '—' : purpose.replaceAll('_', ' ');
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
		case 'bar_settling':
			return 'newest candle settling';
		case 'user_feed_gate':
			return 'user-order feed not connected';
		case 'catch_up':
			return 'catching up on missed bars';
		case 'entries_disabled':
			return 'new entries disabled';
		case 'entry_geometry':
			return 'signal matched but the stop/target geometry was illegal';
		case 'entry_sizing':
			return 'signal matched but sizing rested no order';
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
		case 'signal':
			return 'signal exit';
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
	const target =
		position.target_price === null || position.target_price === undefined
			? 'no take-profit'
			: `target ${position.target_price}`;
	return `${position.side} ${position.quantity} @ ${position.entry_price} · stop ${position.stop_price} · ${target}`;
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
