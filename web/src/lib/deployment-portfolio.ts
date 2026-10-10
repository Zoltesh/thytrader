/**
 * Portfolio page view model (`/deployments`, slice 3 of the UI redesign).
 *
 * Every figure derives from the existing deployment payload (and, for names,
 * the strategy library). Nothing here invents a number: a metric that cannot
 * be computed truthfully is `unavailable` and renders as `—` with its reason.
 * Paper capital is simulated, so money is never totalled across paper and
 * live, and amounts are only summed within one quote currency. Paper futures
 * books (USD, ADR 0129) form their own `USD futures` bucket: they are never added
 * to USDC, USDT, or spot USD amounts.
 */
import { canonicalPositions, type Deployment, type DeploymentPosition } from './deployments';
import { marketLabel, productIdQuote, workingOrderCount } from './deployment-detail';
import { protectionBadge } from './protection-evidence';
import { lifecycleControlsAvailable } from './lifecycle-contract';
import { sumDecimalStrings } from './money';
import { compareDecimalStrings, formatUsd } from './portfolio';
import { isFuturesProductId } from './product-id';
import type { StrategyLibraryEntry } from './strategies';
import { rulesLabel, rulesState, shortStrategyFingerprint } from './strategy-workspace';

export type ModeFilter = 'all' | 'paper' | 'live';

export const MODE_FILTERS: readonly { id: ModeFilter; label: string }[] = [
	{ id: 'all', label: 'All' },
	{ id: 'paper', label: 'Paper' },
	{ id: 'live', label: 'Live' }
];

export function filterByMode(rows: readonly Deployment[], filter: ModeFilter): Deployment[] {
	return filter === 'all' ? [...rows] : rows.filter((row) => row.mode === filter);
}

export type GroupKey = 'attention' | 'running' | 'paused' | 'stopped';

export const GROUP_ORDER: readonly { key: GroupKey; label: string }[] = [
	{ key: 'attention', label: 'Needs attention' },
	{ key: 'running', label: 'Running' },
	{ key: 'paused', label: 'Paused' },
	{ key: 'stopped', label: 'Stopped' }
];

/**
 * Group membership (unchanged from the earlier Deployments page): any status
 * other than running, paused, or stopped needs attention.
 */
export function groupOf(deployment: Deployment): GroupKey {
	if (deployment.status === 'running') return 'running';
	if (deployment.status === 'paused') return 'paused';
	if (deployment.status === 'stopped') return 'stopped';
	return 'attention';
}

export function groupDeployments(rows: readonly Deployment[]): Record<GroupKey, Deployment[]> {
	const groups: Record<GroupKey, Deployment[]> = {
		attention: [],
		running: [],
		paused: [],
		stopped: []
	};
	for (const row of rows) groups[groupOf(row)].push(row);
	return groups;
}

export type StrategyIdentity = { name: string; currentFingerprint: string | null };

/** Strategy id → current name and current rules fingerprint, from the library. */
export function strategyIdentityIndex(
	entries: readonly StrategyLibraryEntry[]
): Map<string, StrategyIdentity> {
	const index = new Map<string, StrategyIdentity>();
	for (const entry of entries) {
		index.set(entry.strategy_id, {
			name: entry.name,
			currentFingerprint: entry.current_fingerprint
		});
	}
	return index;
}

export type QuoteTotal = { quote: string; amount: string };

export type MoneyMetric =
	| { state: 'value'; totals: QuoteTotal[]; reporting: number; of: number }
	| { state: 'unavailable'; reason: string };

export type PortfolioHeaderMetrics = {
	running: number;
	paused: number;
	attention: number;
	stopped: number;
	allocated: MoneyMetric;
	equity: MoneyMetric;
	exposure: MoneyMetric;
};

/** Paper futures books settle in USD and are totalled only with each other. */
export const FUTURES_QUOTE = 'USD futures';

const MIXED_MODES =
	'Choose Paper or Live: simulated paper capital is never totalled with real money.';

/** Currency bucket of a bot's amounts; futures books are USD kept apart from spot. */
function quoteOf(deployment: Deployment): string {
	if (isFuturesProductId(deployment.product_id)) return FUTURES_QUOTE;
	return productIdQuote(deployment.product_id) ?? 'unknown quote';
}

function isDecimal(value: string): boolean {
	return /^-?\d+(?:\.\d+)?$/.test(value);
}

function abs(value: string): string {
	return value.startsWith('-') ? value.slice(1) : value;
}

/** Sum per quote currency; any malformed amount makes the whole metric unavailable. */
function totalsByQuote(amounts: readonly { quote: string; amount: string }[]): QuoteTotal[] | null {
	const byQuote = new Map<string, string[]>();
	for (const item of amounts) {
		if (!isDecimal(item.amount)) return null;
		byQuote.set(item.quote, [...(byQuote.get(item.quote) ?? []), item.amount]);
	}
	return [...byQuote.entries()]
		.sort(([left], [right]) => left.localeCompare(right))
		.map(([quote, values]) => ({ quote, amount: sumDecimalStrings(values) }));
}

function capitalMetric(
	active: readonly Deployment[],
	field: 'allocated_capital' | 'performance_equity',
	missingReason: string
): MoneyMetric {
	if (active.length === 0) return { state: 'unavailable', reason: 'No running or paused bots.' };
	const amounts = active.flatMap((deployment) => {
		const value = deployment.capital?.[field];
		return value === null || value === undefined || value === ''
			? []
			: [{ quote: quoteOf(deployment), amount: value }];
	});
	if (amounts.length === 0) return { state: 'unavailable', reason: missingReason };
	const totals = totalsByQuote(amounts);
	if (totals === null)
		return { state: 'unavailable', reason: 'A capital value was not a decimal amount.' };
	return { state: 'value', totals, reporting: amounts.length, of: active.length };
}

/**
 * Gross marked exposure of open inventory.
 *
 * A flat book contributes zero. An open single book needs a complete mark
 * (`ledger.mark_complete` and `ledger.marked_exposure`); a multi-book
 * deployment's ledger mark covers only its focus product, so it is unknown.
 * One unknown book makes the aggregate unknown rather than understated.
 */
function exposureMetric(active: readonly Deployment[]): MoneyMetric {
	if (active.length === 0) return { state: 'unavailable', reason: 'No running or paused bots.' };
	const amounts: { quote: string; amount: string }[] = [];
	for (const deployment of active) {
		const positions = canonicalPositions(deployment);
		if (positions.length === 0) {
			amounts.push({ quote: quoteOf(deployment), amount: '0' });
			continue;
		}
		const marked = deployment.ledger?.marked_exposure ?? null;
		if (positions.length > 1 || deployment.ledger?.mark_complete !== true || marked === null) {
			return {
				state: 'unavailable',
				reason: 'An open position has no complete mark, so exposure is not shown.'
			};
		}
		amounts.push({ quote: quoteOf(deployment), amount: abs(marked) });
	}
	const totals = totalsByQuote(amounts);
	if (totals === null)
		return { state: 'unavailable', reason: 'An exposure value was not a decimal amount.' };
	return { state: 'value', totals, reporting: active.length, of: active.length };
}

/**
 * Header metrics for the selected mode filter.
 *
 * Counts cover every row passed in. Money covers running and paused bots
 * only, and is unavailable when the rows mix paper and live.
 */
export function portfolioHeaderMetrics(
	rows: readonly Deployment[],
	filter: ModeFilter
): PortfolioHeaderMetrics {
	const scoped = filterByMode(rows, filter);
	const groups = groupDeployments(scoped);
	const active = scoped.filter((deployment) => deployment.status !== 'stopped');
	const mixed = new Set(active.map((deployment) => deployment.mode)).size > 1;
	const mixedMetric: MoneyMetric = { state: 'unavailable', reason: MIXED_MODES };
	return {
		running: groups.running.length,
		paused: groups.paused.length,
		attention: groups.attention.length,
		stopped: groups.stopped.length,
		allocated: mixed
			? mixedMetric
			: capitalMetric(active, 'allocated_capital', 'No bot reports allocated capital.'),
		equity: mixed
			? mixedMetric
			: capitalMetric(active, 'performance_equity', 'No bot reports performance equity.'),
		exposure: mixed ? mixedMetric : exposureMetric(active)
	};
}

/** `1,234.50` style quote amount (display only; sums stay exact strings). */
export function formatQuoteAmount(amount: string): string {
	return formatUsd(amount).replace('$', '');
}

/** One-line reading of a money metric; unavailable renders as an em dash. */
export function moneyMetricText(metric: MoneyMetric): string {
	if (metric.state === 'unavailable') return '—';
	return metric.totals
		.map((total) => `${formatQuoteAmount(total.amount)} ${total.quote}`)
		.join(' · ');
}

/** Secondary line under a money metric: its reason, or partial coverage. */
export function moneyMetricNote(metric: MoneyMetric): string | null {
	if (metric.state === 'unavailable') return metric.reason;
	if (metric.reporting < metric.of) return `${metric.reporting} of ${metric.of} bots report it`;
	return null;
}

export type PortfolioRow = {
	id: string;
	name: string;
	/** `Current rules` / `Earlier edit`, or null when it cannot be resolved. */
	rules: string | null;
	mode: 'paper' | 'live';
	modeLabel: 'Paper' | 'LIVE';
	market: string;
	clock: string;
	position: string;
	protection: string;
	pnl: string;
	pnlTone: 'pos' | 'neg' | 'muted';
	status: string;
	note: string | null;
	readOnly: boolean;
};

function baseOf(productId: string): string {
	const separator = productId.indexOf('-');
	return separator === -1 ? productId : productId.slice(0, separator);
}

function capitalize(text: string): string {
	return text === '' ? text : `${text[0].toUpperCase()}${text.slice(1)}`;
}

/** `Long 0.0041 BTC`, `Flat`, or `2 open books`. */
export function positionText(positions: readonly DeploymentPosition[]): string {
	if (positions.length === 0) return 'Flat';
	if (positions.length > 1) return `${positions.length} open books`;
	const position = positions[0];
	return `${capitalize(position.side ?? 'long')} ${position.quantity} ${baseOf(position.product_id)}`;
}

/** Protective stop/target and protection status, or working orders when flat. */
export function protectionText(
	deployment: Deployment,
	positions: readonly DeploymentPosition[]
): string {
	if (positions.length === 0) {
		const working = workingOrderCount(deployment);
		return working === 0
			? 'No open exposure'
			: `${working} working order${working === 1 ? '' : 's'}`;
	}
	if (positions.length > 1) {
		if (deployment.position_state === 'exiting') return 'Exiting';
		const statuses = [...new Set(positions.map((item) => protectionBadge(item).text))];
		return `Protection: ${statuses.join(', ')}`;
	}
	const position = positions[0];
	const badge = protectionBadge(position, { fallback: 'sentence' });
	const detail = badge.detail ? ` · ${badge.detail}` : '';
	return `Stop ${position.stop_price} · TP ${position.target_price ?? 'none'} · ${badge.text}${detail}`;
}

/** Signed fill-ledger net PnL in the product's quote, or `—`. */
export function pnlOf(deployment: Deployment): { text: string; tone: PortfolioRow['pnlTone'] } {
	const net = deployment.ledger?.total_net_pnl ?? null;
	if (net === null || !isDecimal(net)) return { text: '—', tone: 'muted' };
	const sign = compareDecimalStrings(net, '0');
	const text = `${sign > 0 ? '+' : ''}${formatQuoteAmount(net)} ${quoteOf(deployment)}`;
	return { text, tone: sign > 0 ? 'pos' : sign < 0 ? 'neg' : 'muted' };
}

function noteOf(deployment: Deployment, readOnly: boolean): string | null {
	if (deployment.mismatch_detail) return deployment.mismatch_detail;
	const latched = [
		deployment.daily_loss_latched ? 'daily-loss breaker latched' : null,
		deployment.drawdown_latched ? 'drawdown breaker latched' : null
	].filter((item): item is string => item !== null);
	if (latched.length > 0) return capitalize(latched.join(' · '));
	if (readOnly) return 'Read-only: lifecycle contract incomplete';
	const trades = deployment.ledger?.trade_count;
	if (trades !== undefined) return `${trades} closed trade${trades === 1 ? '' : 's'}`;
	return null;
}

/**
 * Name and rules state for a row: library lookup by strategy id, else the name
 * captured at start, else honest fallbacks. A kept live book of a deleted
 * strategy is labelled "(deleted strategy)".
 */
export function rowIdentity(
	deployment: Deployment,
	index: ReadonlyMap<string, StrategyIdentity>
): { name: string; rules: string | null } {
	if (deployment.kind === 'discretionary' || deployment.strategy_fingerprint === null) {
		return { name: 'Discretionary order', rules: null };
	}
	if (deployment.strategy_deleted === true || deployment.strategy_id === null) {
		const name = deployment.strategy_name ?? null;
		return {
			name:
				deployment.strategy_deleted === true
					? `${name ?? 'Strategy'} (deleted strategy)`
					: (name ?? `Strategy ${shortStrategyFingerprint(deployment.strategy_fingerprint)}`),
			rules: null
		};
	}
	const known = index.get(deployment.strategy_id);
	const name =
		known?.name ??
		deployment.strategy_name ??
		`Strategy ${shortStrategyFingerprint(deployment.strategy_fingerprint)}`;
	const state = rulesState(deployment.strategy_fingerprint, known?.currentFingerprint ?? null);
	return { name, rules: state === 'unknown' ? null : rulesLabel(state) };
}

export function portfolioRow(
	deployment: Deployment,
	index: ReadonlyMap<string, StrategyIdentity>
): PortfolioRow {
	const positions = canonicalPositions(deployment);
	const readOnly = !lifecycleControlsAvailable(deployment);
	const identity = rowIdentity(deployment, index);
	const pnl = pnlOf(deployment);
	return {
		id: deployment.id,
		name: identity.name,
		rules: identity.rules,
		mode: deployment.mode,
		modeLabel: deployment.mode === 'live' ? 'LIVE' : 'Paper',
		market: marketLabel(deployment.product_id),
		clock: deployment.timeframe ?? '—',
		position: positionText(positions),
		protection: protectionText(deployment, positions),
		pnl: pnl.text,
		pnlTone: pnl.tone,
		status: capitalize(deployment.status),
		note: noteOf(deployment, readOnly),
		readOnly
	};
}
