/**
 * Review aside for the Trade ticket (slice 3 of the UI redesign).
 *
 * Exact rational arithmetic over the ticket's decimal strings (no binary
 * floats). Only what the ticket itself proves is shown: a marketable entry
 * has no known price, so its loss at stop and reward:risk are `Unknown`.
 * Fees, slippage, and the server's risk checks are not modelled here.
 */
import { groupIntegerDigits } from './money';

type Rational = { num: bigint; den: bigint };

function parse(value: string): Rational | null {
	const match = /^(\d+)(?:\.(\d+))?$/.exec(value.trim());
	if (match === null) return null;
	const fraction = match[2] ?? '';
	return { num: BigInt(`${match[1]}${fraction}`), den: 10n ** BigInt(fraction.length) };
}

function sub(left: Rational, right: Rational): Rational {
	return { num: left.num * right.den - right.num * left.den, den: left.den * right.den };
}

function mul(left: Rational, right: Rational): Rational {
	return { num: left.num * right.num, den: left.den * right.den };
}

function div(left: Rational, right: Rational): Rational {
	return { num: left.num * right.den, den: left.den * right.num };
}

function absolute(value: Rational): Rational {
	return { num: value.num < 0n ? -value.num : value.num, den: value.den };
}

function sign(value: Rational): number {
	return value.num === 0n ? 0 : value.num > 0n ? 1 : -1;
}

/** Round half up to two decimals and group the integer digits. */
function format2(value: Rational): string {
	const negative = value.num < 0n !== value.den < 0n && value.num !== 0n;
	const num = value.num < 0n ? -value.num : value.num;
	const den = value.den < 0n ? -value.den : value.den;
	let cents = (num * 100n) / den;
	if (((num * 100n) % den) * 2n >= den) cents += 1n;
	const digits = cents.toString().padStart(3, '0');
	const text = `${digits.slice(0, -2)}.${digits.slice(-2)}`;
	return `${negative ? '-' : ''}${groupIntegerDigits(text)}`;
}

export type TradeReviewInput = {
	productId: string;
	side: 'long' | 'short';
	entryKind: 'post_only_limit' | 'marketable';
	limitPrice: string;
	quantity: string;
	quoteNotional: string;
	stopPrice: string;
	takeProfitPrice: string;
};

export type TradeReview = {
	entry: string;
	maxLoss: string;
	rewardRisk: string;
	/** Non-blocking consistency notes; the server still validates the order. */
	warnings: string[];
};

function quoteOf(productId: string): string {
	const separator = productId.indexOf('-');
	return separator === -1 ? 'quote' : productId.slice(separator + 1);
}

export function tradeReview(input: TradeReviewInput): TradeReview {
	const quote = quoteOf(input.productId);
	const entry = input.entryKind === 'post_only_limit' ? parse(input.limitPrice) : null;
	const stop = parse(input.stopPrice);
	const target = parse(input.takeProfitPrice);
	const quantity = parse(input.quantity);
	const notional = parse(input.quoteNotional);
	const warnings: string[] = [];

	const entryText =
		input.entryKind === 'marketable'
			? 'Marketable · fills at the market price'
			: entry === null
				? 'Unknown until a limit price is entered'
				: `${format2(entry)} ${quote} post-only limit`;

	let maxLoss = 'Unknown';
	let rewardRisk = 'Unknown';
	if (entry !== null && stop !== null) {
		const riskPerUnit = absolute(sub(entry, stop));
		const direction = sign(sub(entry, stop));
		if (input.side === 'long' && direction <= 0) {
			warnings.push('For a long, the stop loss should be below the entry.');
		}
		if (input.side === 'short' && direction >= 0) {
			warnings.push('For a short, the stop loss should be above the entry.');
		}
		const size = quantity !== null ? quantity : notional !== null ? div(notional, entry) : null;
		if (size !== null && sign(riskPerUnit) !== 0 && sign(entry) !== 0) {
			maxLoss = `${format2(mul(size, riskPerUnit))} ${quote} before fees`;
		}
		if (target !== null && sign(riskPerUnit) !== 0) {
			const targetDirection = sign(sub(target, entry));
			if (input.side === 'long' && targetDirection <= 0) {
				warnings.push('For a long, the take profit should be above the entry.');
			}
			if (input.side === 'short' && targetDirection >= 0) {
				warnings.push('For a short, the take profit should be below the entry.');
			}
			rewardRisk = format2(div(absolute(sub(target, entry)), riskPerUnit));
		}
	}
	return { entry: entryText, maxLoss, rewardRisk, warnings };
}
