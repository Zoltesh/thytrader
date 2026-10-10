/**
 * Home KPI tiles and the header connection line (ADR 0084).
 *
 * Every figure comes from an existing endpoint. A figure that cannot be known
 * is `—` with the reason, never a guess; money is never totalled across paper
 * and live, and quote amounts are only summed within one quote currency.
 */
import type { CoinbaseCredentialsStatus } from '$lib/credentials';
import { productIdQuote } from '$lib/deployment-detail';
import {
	formatQuoteAmount,
	portfolioHeaderMetrics,
	type MoneyMetric
} from '$lib/deployment-portfolio';
import { canonicalPositions, type Deployment } from '$lib/deployments';
import { sumDecimalStrings } from '$lib/money';
import {
	compareDecimalStrings,
	formatUsd,
	permissionLabel,
	type HistoryEntry,
	type Portfolio
} from '$lib/portfolio';
import { portfolioWindowSummary } from '$lib/portfolio-summary';
import type { RiskPolicySnapshot } from '$lib/strategy-workspace';
import type { HistoryRead } from './home-data';
import { displayMinus, formatAge, formatShortUtc } from './home-format';
import type { Load } from './load';

export type TileTone = 'pos' | 'neg' | 'warn' | 'muted';
/** Glyphs pair with words so direction and warnings never rely on color. */
export type TileGlyph = 'up' | 'down' | 'flat' | 'warn';
export type TileLine = { text: string; tone: TileTone; glyph?: TileGlyph };

export type TileView =
	| { kind: 'loading' }
	| { kind: 'value'; value: string; unit: string | null; lines: TileLine[] }
	| { kind: 'unknown'; reason: string; lines: TileLine[]; retryable: boolean };

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
/** A balance reading older than this gets the stale-snapshot disclosure. */
export const STALE_SNAPSHOT_MS = 10 * MINUTE_MS;

const DEMO_LINE: TileLine = {
	text: 'Demo data, not your Coinbase balance',
	tone: 'warn',
	glyph: 'warn'
};
const CONNECT_REASON = 'Connect Coinbase to see your balances.';

function isDecimal(value: string | null | undefined): value is string {
	return typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value);
}

function unknown(reason: string, retryable: boolean, lines: TileLine[] = []): TileView {
	return { kind: 'unknown', reason, lines, retryable };
}

function isFreshInstall(portfolio: Portfolio): boolean {
	return portfolio.demo && portfolio.assets.length === 0;
}

/** `+$42.10`, `−$12.00`, or `$0.00`. */
function signedUsd(amount: string): string {
	const sign = compareDecimalStrings(amount, '0');
	if (sign > 0) return `+${formatUsd(amount)}`;
	return displayMinus(formatUsd(amount));
}

type ValuePoint = { amount: string; asOf: string; ms: number };

function point(amount: string, asOf: string): ValuePoint | null {
	const ms = Date.parse(asOf);
	return Number.isFinite(ms) && isDecimal(amount) ? { amount, asOf, ms } : null;
}

/**
 * The exact per-currency totals behind the USD-pegged value, when the API reports
 * them. The headline adds USD, USDC and USDT 1:1, so it says so.
 */
function peggedTotalsLine(portfolio: Portfolio | null): TileLine | null {
	const totals = portfolio?.totals ?? [];
	if (totals.length === 0) return null;
	const parts = totals.map((total) => `${formatQuoteAmount(total.amount)} ${total.currency}`);
	return { text: `≈ ${parts.join(' + ')}, counted 1:1`, tone: 'muted' };
}

function changeUnavailable(history: Load<HistoryRead>): string {
	if (history.status === 'loading') return '24h change: loading…';
	if (history.status === 'error') return '24h change: — · history could not be loaded';
	if (history.data.kind === 'unavailable') return '24h change: — · history is off on this install';
	return '24h change: — · needs two snapshots in 24h';
}

/**
 * Portfolio value with its 24h change.
 *
 * The value is the newest of the current Coinbase reading and the newest
 * 24h-history snapshot; the change runs from the oldest snapshot in the 24h
 * window to that value, and says "since HH:MM" when history is shorter than a
 * day. A demo portfolio shows its demo total and no change (history records
 * live balances only).
 */
export function portfolioValueTile(input: {
	portfolio: Load<Portfolio>;
	history: Load<HistoryRead>;
	nowMs: number;
}): TileView {
	const current = input.portfolio.data;
	if (current !== null && current.demo) {
		if (isFreshInstall(current)) return unknown(CONNECT_REASON, false);
		const pegged = peggedTotalsLine(current);
		return {
			kind: 'value',
			value: formatUsd(current.total_value.amount),
			unit: null,
			lines: [
				DEMO_LINE,
				{ text: '24h change: — · history records live balances only', tone: 'muted' },
				...(pegged === null ? [] : [pegged])
			]
		};
	}
	const entries: HistoryEntry[] =
		input.history.data?.kind === 'ready' ? input.history.data.history.entries : [];
	const snapshots = entries
		.map((entry) => point(entry.total_value.amount, entry.as_of))
		.filter((item): item is ValuePoint => item !== null)
		.sort((left, right) => right.ms - left.ms);
	const reading = current === null ? null : point(current.total_value.amount, current.as_of);
	const newestSnapshot = snapshots[0] ?? null;
	const value =
		reading !== null && (newestSnapshot === null || reading.ms >= newestSnapshot.ms)
			? reading
			: newestSnapshot;
	if (value === null) {
		if (input.portfolio.status === 'loading' || input.history.status === 'loading') {
			return { kind: 'loading' };
		}
		return unknown(
			input.portfolio.status === 'error'
				? `Coinbase balances could not be loaded: ${input.portfolio.error}`
				: 'No balance reading or snapshot is available yet.',
			input.portfolio.status === 'error'
		);
	}
	const lines: TileLine[] = [];
	const baseline = snapshots.at(-1) ?? null;
	if (baseline !== null && baseline.ms < value.ms) {
		const summary = portfolioWindowSummary([
			{ as_of: value.asOf, total_value: { amount: value.amount, currency: 'USD' } },
			{ as_of: baseline.asOf, total_value: { amount: baseline.amount, currency: 'USD' } }
		]);
		const window =
			value.ms - baseline.ms >= 23 * HOUR_MS ? '24h' : `since ${formatShortUtc(baseline.asOf)}`;
		const percent =
			summary.changePercent === null ? '' : ` (${displayMinus(summary.changePercent)})`;
		lines.push({
			text: `${signedUsd(summary.changeAmount)}${percent} · ${window}`,
			tone: summary.direction === 'gain' ? 'pos' : summary.direction === 'loss' ? 'neg' : 'muted',
			glyph: summary.direction === 'gain' ? 'up' : summary.direction === 'loss' ? 'down' : 'flat'
		});
	} else {
		lines.push({ text: changeUnavailable(input.history), tone: 'muted' });
	}
	const pegged = value === reading ? peggedTotalsLine(current) : null;
	if (pegged !== null) lines.push(pegged);
	const age = input.nowMs - value.ms;
	if (age > STALE_SNAPSHOT_MS) {
		lines.push({
			text: `Snapshot ${formatAge(age)} old · refresh for current balances`,
			tone: 'warn',
			glyph: 'warn'
		});
	}
	return { kind: 'value', value: formatUsd(value.amount), unit: null, lines };
}

function reservedLine(deployments: Load<Deployment[]>, quote: string): TileLine {
	const rows = deployments.data;
	if (rows === null) {
		return {
			text:
				deployments.status === 'error'
					? 'Reserved by live bots: — · bots could not be loaded'
					: 'Reserved by live bots: checking…',
			tone: 'muted'
		};
	}
	const live = rows.filter(
		(deployment) =>
			deployment.mode === 'live' &&
			deployment.status !== 'stopped' &&
			productIdQuote(deployment.product_id) === quote
	);
	if (live.length === 0) return { text: 'Reserved by live bots: none', tone: 'muted' };
	const amounts = live
		.map((deployment) => deployment.capital?.allocated_capital ?? null)
		.filter(isDecimal);
	if (amounts.length === 0) {
		return { text: 'Reserved by live bots: — · no live bot reports an allocation', tone: 'muted' };
	}
	const partial =
		amounts.length < live.length ? ` · ${amounts.length} of ${live.length} bots report it` : '';
	return {
		text: `Reserved by live bots: ${formatQuoteAmount(sumDecimalStrings(amounts))} ${quote}${partial}`,
		tone: 'muted'
	};
}

/**
 * Available to trade in the installation quote currency (the risk policy's
 * `quote_currency`): the Coinbase `available` balance, plus what running and
 * paused live bots reserve (their `capital.allocated_capital`).
 */
export function availableToTradeTile(input: {
	portfolio: Load<Portfolio>;
	riskPolicy: Load<RiskPolicySnapshot>;
	deployments: Load<Deployment[]>;
}): TileView {
	const policy = input.riskPolicy.data;
	if (policy === null) {
		if (input.riskPolicy.status === 'loading') return { kind: 'loading' };
		return unknown('Quote currency unknown: the risk policy could not be read.', true);
	}
	const quote = policy.quote_currency;
	const current = input.portfolio.data;
	if (current === null) {
		if (input.portfolio.status === 'loading') return { kind: 'loading' };
		return unknown('Coinbase balances could not be loaded.', true);
	}
	if (isFreshInstall(current)) return unknown(CONNECT_REASON, false);
	const asset = current.assets.find((candidate) => candidate.currency === quote) ?? null;
	const lines: TileLine[] = [reservedLine(input.deployments, quote)];
	if (current.demo) lines.push(DEMO_LINE);
	else if (asset === null) {
		lines.push({ text: `No ${quote} balance in the latest snapshot`, tone: 'muted' });
	} else if (isDecimal(asset.hold) && compareDecimalStrings(asset.hold, '0') > 0) {
		lines.push({
			text: `${formatQuoteAmount(asset.hold)} ${quote} on hold for open orders`,
			tone: 'muted'
		});
	}
	if (asset !== null && !isDecimal(asset.available)) {
		return unknown(`The ${quote} balance was not a decimal amount.`, false, lines);
	}
	return {
		kind: 'value',
		value: asset === null ? '0.00' : formatQuoteAmount(asset.available),
		unit: quote,
		lines
	};
}

function protectionLine(live: readonly Deployment[]): TileLine {
	const bots = `${live.length} live bot${live.length === 1 ? '' : 's'}`;
	const positions = live.flatMap((deployment) => canonicalPositions(deployment));
	if (positions.length === 0) return { text: `${bots} · flat`, tone: 'muted' };
	const unprotected = positions.filter((position) => position.protection_status === 'unprotected');
	const unconfirmed = positions.filter(
		(position) =>
			position.protection_status !== 'covered' && position.protection_status !== 'unprotected'
	);
	const open = `${positions.length} open`;
	if (unprotected.length > 0) {
		return {
			text: `${bots} · ${open} · ${unprotected.length} unprotected`,
			tone: 'neg',
			glyph: 'warn'
		};
	}
	if (unconfirmed.length > 0) {
		return { text: `${bots} · ${open} · protection unconfirmed`, tone: 'warn', glyph: 'warn' };
	}
	return { text: `${bots} · ${open} · protected`, tone: 'muted' };
}

function moneyValue(metric: Extract<MoneyMetric, { state: 'value' }>): {
	value: string;
	unit: string | null;
} {
	const [only, ...rest] = metric.totals;
	if (only !== undefined && rest.length === 0) {
		return { value: formatQuoteAmount(only.amount), unit: only.quote };
	}
	return {
		value: metric.totals
			.map((total) => `${formatQuoteAmount(total.amount)} ${total.quote}`)
			.join(' · '),
		unit: null
	};
}

/**
 * Live exposure: gross marked exposure of running and paused live bots (per
 * quote currency), their count, and whether open books have exit cover.
 */
export function liveExposureTile(deployments: Load<Deployment[]>): TileView {
	const rows = deployments.data;
	if (rows === null) {
		return deployments.status === 'loading'
			? { kind: 'loading' }
			: unknown('Bots could not be loaded.', true);
	}
	const live = rows.filter(
		(deployment) => deployment.mode === 'live' && deployment.status !== 'stopped'
	);
	if (live.length === 0) {
		return {
			kind: 'value',
			value: 'None',
			unit: null,
			lines: [{ text: 'No running or paused live bots', tone: 'muted' }]
		};
	}
	const exposure = portfolioHeaderMetrics(rows, 'live').exposure;
	const line = protectionLine(live);
	if (exposure.state === 'unavailable') return unknown(exposure.reason, false, [line]);
	return { kind: 'value', ...moneyValue(exposure), lines: [line] };
}

/**
 * Bot counts: running, paused, and the distinct bots that appear in Needs
 * attention (`null` while that list is still being built).
 */
export function botsTile(deployments: Load<Deployment[]>, attentionBots: number | null): TileView {
	const rows = deployments.data;
	if (rows === null) {
		return deployments.status === 'loading'
			? { kind: 'loading' }
			: unknown('Bots could not be loaded.', true);
	}
	const counts = portfolioHeaderMetrics(rows, 'all');
	const attention =
		attentionBots === null
			? 'checking what needs attention…'
			: `${attentionBots} ${attentionBots === 1 ? 'needs' : 'need'} attention`;
	return {
		kind: 'value',
		value: String(counts.running),
		unit: 'running',
		lines: [
			{
				text: `${counts.paused} paused · ${attention}`,
				tone: attentionBots !== null && attentionBots > 0 ? 'warn' : 'muted',
				glyph: attentionBots !== null && attentionBots > 0 ? 'warn' : undefined
			}
		]
	};
}

export type ConnectionLine = { tone: 'ok' | 'warn' | 'error' | 'muted'; text: string };

/** One line under the Home title: Coinbase connection, permissions, and snapshot age. */
export function connectionLine(input: {
	portfolio: Load<Portfolio>;
	credentials: Load<CoinbaseCredentialsStatus>;
	nowMs: number;
}): ConnectionLine {
	const current = input.portfolio.data;
	if (input.portfolio.status === 'error') {
		if (current === null) {
			return { tone: 'error', text: `Coinbase unavailable · ${input.portfolio.error}` };
		}
		const age = formatAge(input.nowMs - Date.parse(current.as_of));
		return {
			tone: 'error',
			text: `Coinbase refresh failed · showing the snapshot from ${age} ago`
		};
	}
	if (current === null) {
		const credentials = input.credentials.data;
		if (credentials === null) return { tone: 'muted', text: 'Checking Coinbase…' };
		return {
			tone: 'muted',
			text: credentials.configured
				? 'Coinbase credentials configured · checking the connection…'
				: 'No Coinbase credentials configured · loading demo data…'
		};
	}
	const refreshing = input.portfolio.status === 'loading' ? ' · refreshing…' : '';
	if (current.demo) {
		return { tone: 'warn', text: `Demo data · no Coinbase credentials configured${refreshing}` };
	}
	const permissions =
		current.connection.permissions.map(permissionLabel).join(' + ') || 'no permissions reported';
	const age = formatAge(input.nowMs - Date.parse(current.as_of));
	return {
		tone: 'ok',
		text: `Coinbase connected · ${permissions} · last snapshot ${age} ago${refreshing}`
	};
}
