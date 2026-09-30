/**
 * Shared wording for persisted trade reasons (`thytrader-trade-reason-v1`).
 *
 * Used by the Why stage and the bot detail "Why it traded" timeline. Joins
 * only what the payload proves: a reason names an order only through its
 * server-composed `reconcile` block, never by inference.
 */
import type { TradeReasonRecord } from './memory';

/** Newest first by persistence time; the input is not mutated. */
export function sortTradeReasons(records: readonly TradeReasonRecord[]): TradeReasonRecord[] {
	return [...records].sort((left, right) => right.created_at.localeCompare(left.created_at));
}

export function tradeReasonKindLabel(record: TradeReasonRecord): string {
	switch (record.signal.kind) {
		case 'strategy_entry':
			return 'Entry';
		case 'take_profit':
			return 'Take profit';
		case 'stop':
			return 'Stop';
		case 'time_exit':
			return 'Time exit';
		case 'discretionary':
			return 'Discretionary';
		default:
			return record.signal.kind.replaceAll('_', ' ');
	}
}

/** Visual tone for the timeline kind label; text always carries the meaning too. */
export function tradeReasonTone(record: TradeReasonRecord): 'pos' | 'neg' | 'muted' | 'plain' {
	if (record.risk.decision === 'deny') return 'muted';
	if (record.signal.kind === 'take_profit') return 'pos';
	if (record.signal.kind === 'stop') return 'neg';
	return 'plain';
}

export function tradeReasonReconcileText(record: TradeReasonRecord): string {
	const { reconcile } = record;
	if (!reconcile.ledger_available)
		return 'Execution ledger unavailable; order outcome cannot be reconciled.';
	if (reconcile.unknown_timeout)
		return 'Submission timed out; order outcome unknown until reconciled.';
	if (reconcile.order_id === null || reconcile.order_status === null)
		return 'Intent recorded; no venue-visible order found.';
	const fills = reconcile.fills.length;
	return `Order ${reconcile.order_id.slice(0, 8)} · ${reconcile.order_status} · ${fills} fill${fills === 1 ? '' : 's'}${reconcile.reject_reason ? ` · rejected: ${reconcile.reject_reason}` : ''}`;
}

export function tradeReasonNotesText(record: TradeReasonRecord): string {
	return record.notes.length === 0
		? 'No operator note.'
		: record.notes.map((note) => `${note.origin}: ${note.body}`).join(' · ');
}
