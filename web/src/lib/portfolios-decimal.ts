/**
 * Exact decimal-string math for portfolio weights and money (BigInt, no floats) and
 * the allocation check. Re-exported by `portfolios.ts`.
 */
import { sumDecimalStrings } from './money';
import { compareDecimalStrings, subtractDecimalStrings } from './portfolio';
import type { Portfolio } from './portfolios-types';

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/**
 * Move the decimal point `places` to the right (negative: to the left), exactly.
 * `shiftDecimal('0.3333', 2)` is `33.33`; `shiftDecimal('50', -2)` is `0.5`.
 */
export function shiftDecimal(value: string, places: number): string {
	const match = DECIMAL.exec(value.trim());
	if (!match) throw new Error(`Not a decimal string: ${value}`);
	const [, sign, whole, fraction = ''] = match;
	const digits = `${whole}${fraction}`;
	const point = whole.length + places;
	let integerPart: string;
	let fractionPart: string;
	if (point <= 0) {
		integerPart = '0';
		fractionPart = `${'0'.repeat(-point)}${digits}`;
	} else if (point >= digits.length) {
		integerPart = `${digits}${'0'.repeat(point - digits.length)}`;
		fractionPart = '';
	} else {
		integerPart = digits.slice(0, point);
		fractionPart = digits.slice(point);
	}
	integerPart = integerPart.replace(/^0+(?=\d)/, '');
	fractionPart = fractionPart.replace(/0+$/, '');
	const text = fractionPart === '' ? integerPart : `${integerPart}.${fractionPart}`;
	return /^0(?:\.0*)?$/.test(text) ? '0' : `${sign}${text}`;
}

/** Exact product of two decimal strings (`multiplyDecimals('0.5', '300')` is `150`). */
export function multiplyDecimals(left: string, right: string): string {
	const parse = (value: string): { units: bigint; scale: number } => {
		const match = DECIMAL.exec(value.trim());
		if (!match) throw new Error(`Not a decimal string: ${value}`);
		const [, sign, whole, fraction = ''] = match;
		return { units: BigInt(`${sign}${whole}${fraction}`), scale: fraction.length };
	};
	const a = parse(left);
	const b = parse(right);
	return shiftDecimal((a.units * b.units).toString(), -(a.scale + b.scale));
}

/** A form's percent text (`33.33`) as a fraction (`0.3333`), or null when not a percent. */
export function percentInputToFraction(input: string, maxPlaces = 2): string | null {
	const text = input.trim();
	const pattern = new RegExp(`^\\d+(?:\\.\\d{1,${maxPlaces}})?$`);
	if (!pattern.test(text)) return null;
	return shiftDecimal(text, -2);
}

/** A fraction (`0.3333`) as editable percent text (`33.33`). */
export function fractionToPercentInput(fraction: string): string {
	return shiftDecimal(fraction, 2);
}

/** A positive quote amount with at most eight decimal places, canonicalized; else null. */
export function quoteInput(input: string): string | null {
	const text = input.trim().replace(/,/g, '');
	if (!/^\d+(?:\.\d{1,8})?$/.test(text)) return null;
	const canonical = shiftDecimal(text, 0);
	return compareDecimalStrings(canonical, '0') > 0 ? canonical : null;
}

/** Exact sum of fraction strings. */
export function sumFractions(fractions: readonly string[]): string {
	return fractions.length === 0 ? '0' : sumDecimalStrings([...fractions]);
}

export type AllocationCheck = {
	/** Sleeve weights plus the reserve, as a fraction. */
	total: string;
	/** What is left unallocated (negative when over). */
	remaining: string;
	ok: boolean;
};

/** Weights plus the cash reserve must not exceed 100% of capital. */
export function checkAllocation(weights: readonly string[], reserve: string): AllocationCheck {
	const total = sumFractions([...weights, reserve]);
	return {
		total,
		remaining: subtractDecimalStrings('1', total),
		ok: compareDecimalStrings(total, '1') <= 0
	};
}

/** Round a decimal string half away from zero to `places` decimals, exactly. */
export function roundDecimal(value: string, places: number): string {
	const match = DECIMAL.exec(value.trim());
	if (!match) throw new Error(`Not a decimal string: ${value}`);
	const [, sign, whole, fraction = ''] = match;
	const units = BigInt(`${whole}${fraction.padEnd(places, '0').slice(0, places)}`);
	const roundUp = (fraction[places] ?? '0') >= '5';
	const rounded = units + (roundUp ? 1n : 0n);
	const digits = rounded.toString().padStart(places + 1, '0');
	const integerPart = digits.slice(0, digits.length - places);
	const fractionPart = places > 0 ? `.${digits.slice(digits.length - places)}` : '';
	const text = `${integerPart}${fractionPart}`;
	return /^0(?:\.0*)?$/.test(text) ? text : `${sign}${text}`;
}

/** How much weight still fits beside the current sleeves and reserve (never negative). */
export function remainingWeight(portfolio: Portfolio): string {
	const check = checkAllocation(
		portfolio.sleeves.map((sleeve) => sleeve.weight_fraction),
		portfolio.cash_reserve_fraction
	);
	return compareDecimalStrings(check.remaining, '0') > 0 ? check.remaining : '0';
}
