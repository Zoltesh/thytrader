/**
 * Pure helpers of the decision timeline (`DecisionTimeline.svelte` and its row
 * components): which journal to read, the pager line, and trade-reason text.
 */
import type { TradeReasonRecord } from '$lib/memory';

/** Which journal to read: one bot, or every bot of a strategy (optionally one). */
export type DecisionTimelineSource =
	| { kind: 'deployment'; deploymentId: string }
	| { kind: 'strategy'; strategyId: string; deploymentId: string | null };

/** Changes whenever the timeline must reload from the newest bar. */
export function decisionSourceKey(source: DecisionTimelineSource): string {
	return source.kind === 'deployment'
		? `deployment:${source.deploymentId}`
		: `strategy:${source.strategyId}:${source.deploymentId ?? '*'}`;
}

/** Pager line under the rows, e.g. `Showing 50 decisions · older decisions available`. */
export function decisionPagerText(count: number, olderAvailable: boolean): string {
	return `Showing ${count} decision${count === 1 ? '' : 's'}${
		olderAvailable ? ' · older decisions available' : ' · start of the journaled history'
	}`;
}

/** The risk verdict a persisted trade reason recorded, with its policy source. */
export function tradeReasonRiskText(reason: TradeReasonRecord): string {
	const detail = reason.risk.detail.trim() === '' ? '' : ` · ${reason.risk.detail}`;
	const policy =
		reason.risk.policy_source === 'published'
			? 'published risk policy'
			: 'compiled default risk policy';
	return `Risk ${reason.risk.decision} (${reason.risk.reason_code})${detail} · ${policy}`;
}
