/**
 * Portfolio value summaries with exact decimal-string arithmetic.
 *
 * Mirrors the API contract: money arrives as canonical decimal strings and
 * histories are newest-first snapshot samples. Percent rounding uses the same
 * BigInt scaling as `formatPercentChange` in portfolio.ts, with an explicit
 * sign so gains are visible, not implicit.
 */
import { compareDecimalStrings, subtractDecimalStrings, type HistoryEntry } from './portfolio';

export type PortfolioWindowSummary = {
	/** Absolute change across the visible window, exact decimal string. */
	changeAmount: string;
	/** Signed percent change with two decimals; null when the baseline is zero. */
	changePercent: string | null;
	direction: 'gain' | 'loss' | 'flat';
	/** Snapshot samples backing the figure; single-sample windows have no change math. */
	sampleCount: number;
};

/**
 * Change across the newest-first history window.
 *
 * Fewer than two samples yields a zero-change summary that discloses the
 * sample count instead of inventing a trend from one point.
 */
export function portfolioWindowSummary(entries: readonly HistoryEntry[]): PortfolioWindowSummary {
	if (entries.length < 2) {
		return {
			changeAmount: '0',
			changePercent: null,
			direction: 'flat',
			sampleCount: entries.length
		};
	}
	const current = entries[0].total_value.amount;
	const baseline = entries[entries.length - 1].total_value.amount;
	const amount = subtractDecimalStrings(current, baseline);
	const percent =
		compareDecimalStrings(baseline, '0') === 0
			? null
			: formatSignedPercentFromRatio(amount, baseline);
	const direction =
		compareDecimalStrings(amount, '0') > 0
			? 'gain'
			: compareDecimalStrings(amount, '0') < 0
				? 'loss'
				: 'flat';
	return { changeAmount: amount, changePercent: percent, direction, sampleCount: entries.length };
}

/**
 * Format amount/baseline as a signed percentage with two decimals.
 */
function formatSignedPercentFromRatio(amount: string, baseline: string): string {
	const amountParts = parseDecimalParts(amount);
	const baselineParts = parseDecimalParts(baseline);
	let numerator = amountParts.units * 10000n;
	let denominator = baselineParts.units;
	const scaleDifference = baselineParts.scale - amountParts.scale;
	if (scaleDifference >= 0) {
		numerator *= 10n ** BigInt(scaleDifference);
	} else {
		denominator *= 10n ** BigInt(-scaleDifference);
	}
	const negative = numerator < 0n !== denominator < 0n;
	const absoluteNumerator = numerator < 0n ? -numerator : numerator;
	const absoluteDenominator = denominator < 0n ? -denominator : denominator;
	let rounded = absoluteNumerator / absoluteDenominator;
	if ((absoluteNumerator % absoluteDenominator) * 2n >= absoluteDenominator) rounded += 1n;
	const text = formatFixedDecimal(negative ? -rounded : rounded, 2);
	return `${text.startsWith('-') ? '' : '+'}${text}%`;
}

function parseDecimalParts(amount: string): { units: bigint; scale: number } {
	const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
	if (!match) {
		throw new Error(`Invalid decimal string: ${amount}`);
	}
	const fraction = match[3] ?? '';
	return {
		units: BigInt(`${match[1] === '-' ? '-' : ''}${match[2]}${fraction}`),
		scale: fraction.length
	};
}

function formatFixedDecimal(units: bigint, scale: number): string {
	const sign = units < 0n ? '-' : '';
	const digits = (units < 0n ? -units : units).toString().padStart(scale + 1, '0');
	const splitAt = digits.length - scale;
	return `${sign}${digits.slice(0, splitAt)}.${digits.slice(splitAt)}`;
}
