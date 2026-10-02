/**
 * Home "Needs attention" (ADR 0084): one list aggregated from existing
 * endpoints. Every item names its problem in words beside an icon (never color
 * alone) and links to where it is fixed. Nothing here mutates.
 *
 * - Bots (`GET /api/v1/deployments`): paused; reporting `mismatch_detail`; any
 *   status other than running/paused/stopped; a tripped daily-loss or drawdown
 *   latch; a live position without confirmed exit cover. Stopped bots are
 *   skipped, except live ones that still report a mismatch or hold an
 *   unprotected position.
 * - Setup, only while live bots run or are paused: no Coinbase credentials
 *   (`GET /api/v1/credentials/coinbase`); no published risk policy
 *   (`GET /api/v1/risk-policy` `source`).
 * - Watched datasets (`GET /api/v1/operator/data-catalog`): latest worker
 *   attempt failed; a backfill that is stuck (its scheduled attempt is overdue
 *   or keeps failing, from `GET /api/v1/market-data/ingestion`); stale; gaps in
 *   the verified range. One item per dataset, worst problem first.
 * - Research (`GET /api/v1/research/jobs`): the newest job of a recently
 *   updated strategy failed within the last week.
 */
import type { CoinbaseCredentialsStatus } from '$lib/credentials';
import { marketLabel } from '$lib/deployment-detail';
import { rowIdentity, type StrategyIdentity } from '$lib/deployment-portfolio';
import { canonicalPositions, type Deployment } from '$lib/deployments';
import type { StrategyLibraryEntry } from '$lib/strategies';
import type { RiskPolicySnapshot } from '$lib/strategy-workspace';
import {
	datasetKey,
	type DatasetCoverageRow,
	type IngestionState,
	type ResearchJob,
	type ResearchScan
} from './home-data';
import { formatShortUtc, truncate } from './home-format';

export type AttentionSeverity = 'critical' | 'warning';

export type AttentionIcon =
	| 'pause'
	| 'mismatch'
	| 'status'
	| 'breaker'
	| 'shield'
	| 'key'
	| 'policy'
	| 'data'
	| 'clock'
	| 'gap'
	| 'research';

export type AttentionHref =
	`/deployments/${string}` | `/strategies/${string}` | '/settings' | '/chat';

export type AttentionAction =
	{ label: string; href: AttentionHref } | { label: string; fragment: 'data-health' };

export type AttentionCategory = 'setup' | 'bot' | 'data' | 'research';

export type AttentionItem = {
	/** Stable key, e.g. `bot-paused:{deployment id}`. */
	id: string;
	category: AttentionCategory;
	severity: AttentionSeverity;
	icon: AttentionIcon;
	/** Headline naming the problem, e.g. "UNI Momentum paused". */
	label: string;
	/** One supporting line. */
	detail: string;
	action: AttentionAction;
	/** Real-money items sort first and carry a LIVE tag. */
	live: boolean;
	/** Set on bot items so Home can count the bots that need attention. */
	deploymentId?: string;
	/** Set on dataset items: `datasetKey(product, timeframe)` and the worst problem in brief. */
	dataset?: { key: string; problem: string };
};

const DAY_MS = 86_400_000;
/** Grace past a scheduled ingestion attempt before a backfill counts as stuck. */
const STUCK_GRACE_MS = 15 * 60_000;
/** An attempt still "running" this long after it started counts as stuck. */
const STUCK_RUNNING_MS = 60 * 60_000;
/** Consecutive failures that make a still-retrying backfill stuck. */
const STUCK_FAILURES = 2;
/** A failed research job older than this no longer surfaces on Home. */
const RESEARCH_RECENT_MS = 7 * DAY_MS;

function plural(count: number, one: string, many = `${one}s`): string {
	return `${count} ${count === 1 ? one : many}`;
}

function deploymentHref(id: string): `/deployments/${string}` {
	return `/deployments/${encodeURIComponent(id)}`;
}

function strategyTestHref(strategyId: string): `/strategies/${string}` {
	return `/strategies/${encodeURIComponent(strategyId)}/test`;
}

/** `Live · ETH / USDC · 1h` */
function botWhere(deployment: Deployment): string {
	const parts = [deployment.mode === 'live' ? 'Live' : 'Paper', marketLabel(deployment.product_id)];
	if (deployment.timeframe) parts.push(deployment.timeframe);
	return parts.join(' · ');
}

function protectionCounts(deployment: Deployment): { unprotected: number; unconfirmed: number } {
	let unprotected = 0;
	let unconfirmed = 0;
	for (const position of canonicalPositions(deployment)) {
		const status = position.protection_status ?? 'unknown';
		if (status === 'unprotected') unprotected += 1;
		else if (status !== 'covered' && status !== 'flat') unconfirmed += 1;
	}
	return { unprotected, unconfirmed };
}

/** Problems on paper and live bots, linking to each bot's detail page. */
export function deploymentAttention(
	deployments: readonly Deployment[],
	names: ReadonlyMap<string, StrategyIdentity>
): AttentionItem[] {
	const items: AttentionItem[] = [];
	for (const deployment of deployments) {
		const live = deployment.mode === 'live';
		const name = rowIdentity(deployment, names).name;
		const where = botWhere(deployment);
		const base = {
			category: 'bot' as const,
			live,
			deploymentId: deployment.id,
			action: { label: 'Review', href: deploymentHref(deployment.id) }
		};
		const protection = live ? protectionCounts(deployment) : { unprotected: 0, unconfirmed: 0 };
		if (deployment.status === 'stopped') {
			if (live && deployment.mismatch_detail) {
				items.push({
					...base,
					id: `bot-mismatch:${deployment.id}`,
					severity: 'critical',
					icon: 'mismatch',
					label: `${name} stopped with an unresolved mismatch`,
					detail: `${where} · ${deployment.mismatch_detail}`
				});
			}
			if (live && protection.unprotected > 0) {
				items.push({
					...base,
					id: `bot-unprotected:${deployment.id}`,
					severity: 'critical',
					icon: 'shield',
					label: `${name} holds a live position without exit cover`,
					detail: `${where} · stopped · no venue-visible stop or take-profit`
				});
			}
			continue;
		}
		if (deployment.status === 'paused') {
			items.push({
				...base,
				id: `bot-paused:${deployment.id}`,
				severity: 'warning',
				icon: 'pause',
				label: `${name} paused`,
				detail: deployment.mismatch_detail
					? `${where} · ${deployment.mismatch_detail}`
					: `${where} · no new entries until it is resumed`
			});
		} else if (deployment.mismatch_detail) {
			items.push({
				...base,
				id: `bot-mismatch:${deployment.id}`,
				severity: live ? 'critical' : 'warning',
				icon: 'mismatch',
				label: `${name} reports a mismatch`,
				detail: `${where} · ${deployment.mismatch_detail}`
			});
		} else if (deployment.status !== 'running') {
			items.push({
				...base,
				id: `bot-status:${deployment.id}`,
				severity: 'warning',
				icon: 'status',
				label: `${name} is ${deployment.status || 'in an unknown state'}`,
				detail: `${where} · not running, paused, or stopped`
			});
		}
		const latches = [
			deployment.daily_loss_latched ? 'daily-loss' : null,
			deployment.drawdown_latched ? 'drawdown' : null
		].filter((latch): latch is string => latch !== null);
		if (latches.length > 0) {
			items.push({
				...base,
				id: `bot-breaker:${deployment.id}`,
				severity: live ? 'critical' : 'warning',
				icon: 'breaker',
				label: `${name}: ${latches.join(' and ')} breaker tripped`,
				detail: `${where} · risk-increasing entries stay blocked until the latch is reset`
			});
		}
		if (protection.unprotected > 0) {
			items.push({
				...base,
				id: `bot-unprotected:${deployment.id}`,
				severity: 'critical',
				icon: 'shield',
				label: `${name} has a live position without exit cover`,
				detail: `${where} · ${plural(protection.unprotected, 'open book')} with no venue-visible stop or take-profit`
			});
		} else if (protection.unconfirmed > 0) {
			items.push({
				...base,
				id: `bot-unconfirmed:${deployment.id}`,
				severity: 'warning',
				icon: 'shield',
				label: `${name}: exit cover is unconfirmed`,
				detail: `${where} · protective orders are not reconciled yet`
			});
		}
	}
	return items;
}

/** Credentials and risk policy, raised only while live bots are running or paused. */
export function setupAttention(input: {
	deployments: readonly Deployment[];
	credentials: CoinbaseCredentialsStatus | null;
	riskPolicy: RiskPolicySnapshot | null;
}): AttentionItem[] {
	const live = input.deployments.filter(
		(deployment) => deployment.mode === 'live' && deployment.status !== 'stopped'
	);
	if (live.length === 0) return [];
	const bots = `${plural(live.length, 'live bot')} ${live.length === 1 ? 'is' : 'are'} running or paused`;
	const items: AttentionItem[] = [];
	if (input.credentials !== null && !input.credentials.configured) {
		items.push({
			id: 'setup:credentials',
			category: 'setup',
			severity: 'critical',
			icon: 'key',
			live: true,
			label: 'No Coinbase credentials for live bots',
			detail: `${bots} while no Coinbase credentials are configured.`,
			action: { label: 'Add credentials', href: '/settings' }
		});
	}
	if (input.riskPolicy !== null && input.riskPolicy.source !== 'published') {
		items.push({
			id: 'setup:risk-policy',
			category: 'setup',
			severity: 'critical',
			icon: 'policy',
			live: true,
			label: 'No published risk policy',
			detail: `${bots} under the compiled default. Publish one with thytrader-runtime set-risk-policy --confirm, or ask the agent.`,
			action: { label: 'Ask the agent', href: '/chat' }
		});
	}
	return items;
}

/** Watched datasets whose ingestion should be checked for a stuck backfill. */
export function backfillCandidates(rows: readonly DatasetCoverageRow[]): DatasetCoverageRow[] {
	return rows.filter(
		(row) => row.watched && row.watch_status === 'backfilling' && row.worker_status !== 'failed'
	);
}

/**
 * Why a backfill is stuck, or null when it is progressing.
 *
 * Stuck means the worker is not advancing it: the scheduled attempt is overdue
 * by more than one interval (at least 15 minutes), an attempt has been
 * "running" for over an hour, or it keeps failing while it retries.
 */
export function stuckBackfillReason(state: IngestionState, nowMs: number): string | null {
	const last = state.last_attempt_at === null ? Number.NaN : Date.parse(state.last_attempt_at);
	if (state.status === 'running') {
		if (Number.isFinite(last) && nowMs - last > STUCK_RUNNING_MS) {
			return `An attempt has been running since ${formatShortUtc(state.last_attempt_at ?? '')}`;
		}
	} else if (state.next_attempt_at !== null) {
		const due = Date.parse(state.next_attempt_at);
		const interval = Number.isFinite(last) && due > last ? due - last : 0;
		if (Number.isFinite(due) && nowMs - due > Math.max(interval, STUCK_GRACE_MS)) {
			return state.last_attempt_at === null
				? `The scheduled attempt at ${formatShortUtc(state.next_attempt_at)} never ran`
				: `No attempt since ${formatShortUtc(state.last_attempt_at)}; the next was due ${formatShortUtc(state.next_attempt_at)}`;
		}
	}
	if (state.failure !== null && state.failure.consecutive_failures >= STUCK_FAILURES) {
		return `Retrying after ${plural(state.failure.consecutive_failures, 'failure')}: ${truncate(state.failure.message, 100)}`;
	}
	return null;
}

type DatasetProblem = { icon: AttentionIcon; label: string; detail: string; short: string };

function datasetProblems(
	row: DatasetCoverageRow,
	ingestion: IngestionState | null,
	nowMs: number
): DatasetProblem[] {
	const subject = `${row.product_id} ${row.timeframe}`;
	const backfilling = row.watch_status === 'backfilling';
	const problems: DatasetProblem[] = [];
	if (row.worker_status === 'failed') {
		problems.push({
			icon: 'data',
			label: `${subject} ${backfilling ? 'backfill' : 'ingest'} failed`,
			detail:
				row.failure_message ??
				(row.failure_code ? `Failure: ${row.failure_code}` : 'The latest worker attempt failed.'),
			short: 'latest attempt failed'
		});
	} else if (backfilling && ingestion !== null) {
		const reason = stuckBackfillReason(ingestion, nowMs);
		if (reason !== null) {
			const received = row.watch_covered_candle_count ?? row.received_candle_count;
			const expected = row.watch_expected_candle_count ?? row.expected_candle_count;
			const progress =
				received !== null && expected !== null && expected !== undefined
					? ` · ${received} of ${expected} candles`
					: '';
			problems.push({
				icon: 'clock',
				label: `${subject} backfill is stuck`,
				detail: `${reason}${progress}`,
				short: 'backfill stuck'
			});
		}
	}
	if (row.freshness_status === 'stale') {
		problems.push({
			icon: 'clock',
			label: `${subject} data is stale`,
			detail: row.covered_ends_at
				? `Newest verified candle ${formatShortUtc(row.covered_ends_at)}`
				: 'No verified candles yet',
			short: 'stale'
		});
	}
	const gaps = row.gap_count ?? 0;
	const missing = row.missing_intervals ?? 0;
	if (gaps > 0 || missing > 0) {
		const range =
			row.covered_starts_at && row.covered_ends_at
				? ` between ${formatShortUtc(row.covered_starts_at)} and ${formatShortUtc(row.covered_ends_at)}`
				: '';
		problems.push({
			icon: 'gap',
			label:
				missing > 0
					? `${subject} has ${plural(missing, 'missing bar')}`
					: `${subject} has ${plural(gaps, 'gap')}`,
			detail: `${plural(gaps, 'gap')} in the verified range${range}; bars are never interpolated`,
			short: missing > 0 ? plural(missing, 'missing bar') : plural(gaps, 'gap')
		});
	}
	return problems;
}

function datasetAction(
	row: DatasetCoverageRow,
	strategies: readonly StrategyLibraryEntry[]
): { action: AttentionAction; usedBy: string | null } {
	const users = strategies
		.filter(
			(strategy) => strategy.product_id === row.product_id && strategy.timeframe === row.timeframe
		)
		.sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
	const first = users[0];
	if (first === undefined) {
		return { action: { label: 'Data health', fragment: 'data-health' }, usedBy: null };
	}
	return {
		action: { label: 'Open strategy', href: strategyTestHref(first.strategy_id) },
		usedBy:
			users.length === 1
				? `used by ${first.name}`
				: `used by ${first.name} and ${users.length - 1} more`
	};
}

/**
 * One item per watched dataset with a problem: failed, stuck backfill, stale,
 * gaps (worst first; the rest are summarised in the detail line). Linked to the
 * strategy that trades that market and clock, else to Home's Data health.
 */
export function datasetAttention(input: {
	rows: readonly DatasetCoverageRow[];
	ingestion: Readonly<Record<string, IngestionState>>;
	strategies: readonly StrategyLibraryEntry[];
	nowMs: number;
}): AttentionItem[] {
	const items: AttentionItem[] = [];
	for (const row of input.rows) {
		if (!row.watched) continue;
		const key = datasetKey(row.product_id, row.timeframe);
		const problems = datasetProblems(row, input.ingestion[key] ?? null, input.nowMs);
		const [worst, ...rest] = problems;
		if (worst === undefined) continue;
		const { action, usedBy } = datasetAction(row, input.strategies);
		const extras = [...rest.map((problem) => `also ${problem.short}`), usedBy].filter(
			(part): part is string => part !== null
		);
		items.push({
			id: `data:${row.product_id}:${row.timeframe}`,
			category: 'data',
			severity: 'warning',
			icon: worst.icon,
			live: false,
			label: worst.label,
			detail: [worst.detail, ...extras].join(' · '),
			action,
			dataset: { key, problem: `${worst.short.charAt(0).toUpperCase()}${worst.short.slice(1)}` }
		});
	}
	return items;
}

/** Worst problem per dataset key, so Data health and Needs attention always agree. */
export function datasetProblemIndex(items: readonly AttentionItem[]): Record<string, string> {
	const index: Record<string, string> = {};
	for (const item of items) {
		if (item.dataset !== undefined) index[item.dataset.key] = item.dataset.problem;
	}
	return index;
}

function researchFailureDetail(job: ResearchJob): string {
	const phase = job.failed_phase ? `Failed during ${job.failed_phase.replaceAll('_', ' ')}` : null;
	const message = job.failed_detail ?? job.error_message;
	const parts = [phase, message ? truncate(message, 140) : null].filter(
		(part): part is string => part !== null
	);
	return parts.length > 0 ? parts.join(': ') : 'The job failed without a recorded reason.';
}

/** The newest job of a checked strategy failed within the last week. */
export function researchAttention(scan: ResearchScan, nowMs: number): AttentionItem[] {
	const items: AttentionItem[] = [];
	for (const entry of scan.checked) {
		const newest = entry.jobs[0];
		if (newest === undefined || newest.status !== 'failed') continue;
		const updated = Date.parse(newest.updated_at);
		if (!Number.isFinite(updated) || nowMs - updated > RESEARCH_RECENT_MS) continue;
		items.push({
			id: `research:${newest.job_id}`,
			category: 'research',
			severity: 'warning',
			icon: 'research',
			live: false,
			label: `${newest.kind === 'study' ? 'Study' : 'Backtest'} failed · ${entry.strategyName}`,
			detail: `${researchFailureDetail(newest)} · ${formatShortUtc(newest.updated_at)}`,
			action: { label: 'Open Test', href: strategyTestHref(entry.strategyId) }
		});
	}
	return items;
}

const SEVERITY_RANK: Record<AttentionSeverity, number> = { critical: 0, warning: 1 };
const CATEGORY_RANK: Record<AttentionCategory, number> = { setup: 0, bot: 1, data: 2, research: 3 };

/** Critical first, then real money, then setup · bots · data · research, then by headline. */
export function sortAttention(items: readonly AttentionItem[]): AttentionItem[] {
	return [...items].sort(
		(left, right) =>
			SEVERITY_RANK[left.severity] - SEVERITY_RANK[right.severity] ||
			Number(right.live) - Number(left.live) ||
			CATEGORY_RANK[left.category] - CATEGORY_RANK[right.category] ||
			left.label.localeCompare(right.label)
	);
}

/** Distinct bots named by attention items (the Bots tile's "need attention" count). */
export function attentionBotCount(items: readonly AttentionItem[]): number {
	return new Set(
		items.flatMap((item) => (item.deploymentId === undefined ? [] : [item.deploymentId]))
	).size;
}
