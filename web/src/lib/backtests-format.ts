/**
 * Display formatting for backtest results: percents, short fingerprints, recorded
 * fee rates and slippage, spread-stress disclosure, and the optional spread-stress
 * request field. Re-exported by `backtests.ts`.
 */
import { compareDecimalStrings, formatPercent as formatExactPercent, formatUsd } from './portfolio';
import type { BacktestFill, CostAssumptions } from './backtests-types';

export function formatPercent(fraction: string): string {
	return formatExactPercent(fraction);
}

export function shortFingerprint(fingerprint: string): string {
	return `${fingerprint.slice(0, 16)}…${fingerprint.slice(-8)}`;
}

export function isRecordedDecimal(value: string | null | undefined): value is string {
	return typeof value === 'string' && value.length > 0;
}

function formatDisplayFeeRate(rate: string): string {
	try {
		return formatPercent(rate);
	} catch {
		return rate;
	}
}

/** Stop-first on the fill candle; later candles match a resting take-profit first. */
export const SAME_BAR_POLICY_LABEL = 'Fill candle: stop first';

/** The recorded spread stress in bps when it is greater than zero; otherwise null. */
export function spreadStressBps(costs?: CostAssumptions | null): string | null {
	const bps = costs?.spread_bps;
	if (!isRecordedDecimal(bps)) return null;
	try {
		return compareDecimalStrings(bps, '0') > 0 ? bps : null;
	} catch {
		return null;
	}
}

export function formatPublishedCosts(costs?: CostAssumptions | null): string {
	if (!costs) {
		return 'Recorded maker/taker fee rates and fixed_slippage_bps are not included in this response.';
	}
	const maker = isRecordedDecimal(costs.maker_fee_rate)
		? `maker ${formatDisplayFeeRate(costs.maker_fee_rate)}`
		: 'maker fee not recorded';
	const taker = isRecordedDecimal(costs.taker_fee_rate)
		? `taker ${formatDisplayFeeRate(costs.taker_fee_rate)}`
		: 'taker fee not recorded';
	const slippage = isRecordedDecimal(costs.fixed_slippage_bps)
		? `fixed slippage ${costs.fixed_slippage_bps} bps on taker exits`
		: 'fixed_slippage_bps not recorded';
	return `${maker} · ${taker} · ${slippage} (modeled research-run cost assumptions, not observed Coinbase fees)`;
}

/**
 * Spread-stress disclosure for one result, or null when the run was not stressed
 * and recorded no spread cost.
 */
export function formatSpreadCostNote(
	costs: CostAssumptions | null | undefined,
	totalSpreadCost: string | null | undefined
): string | null {
	const bps = spreadStressBps(costs);
	const cost = isRecordedDecimal(totalSpreadCost) ? totalSpreadCost : null;
	if (bps === null && cost === null) return null;
	const parts: string[] = [];
	if (bps !== null) parts.push(`Spread stress ${bps} bps (total bid-ask)`);
	if (cost !== null) parts.push(`total modeled spread cost ${formatUsd(cost)}`);
	return `${parts.join(' · ')}. This is a disclosed stress input, not observed bid/ask data.`;
}

export function formatFillFee(fill: Pick<BacktestFill, 'fee' | 'fee_rate'>): string {
	const amount = formatUsd(fill.fee);
	if (!isRecordedDecimal(fill.fee_rate)) {
		return `${amount} (fee rate not recorded)`;
	}
	return `${amount} (${formatDisplayFeeRate(fill.fee_rate)})`;
}

/**
 * Request field for the optional spread-stress input: omitted when blank or zero
 * (the server default is no stress); any other text is sent for server validation.
 */
export function optionalSpreadStress(raw: string): { spread_bps?: string } {
	const value = raw.trim();
	if (value === '') return {};
	try {
		if (compareDecimalStrings(value, '0') === 0) return {};
	} catch {
		// Not a decimal: send it so the server returns its validation message.
	}
	return { spread_bps: value };
}
