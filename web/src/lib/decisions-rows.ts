/** Decision rows: identity, de-duplicating page merges, and trade-reason assignment. Re-exported by `decisions.ts`. */
import type { TradeReasonRecord } from './memory';
import type { BarDecision } from './decisions-types';

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
