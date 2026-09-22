/**
 * Display formatting for money and quantity strings.
 *
 * Display-only: every helper takes the API's canonical decimal strings and
 * returns presentation text. Sorting, comparison, and rollups keep using the
 * exact strings — rounding never enters the data path.
 */
import { compareDecimalStrings, type PortfolioAsset } from './portfolio';

/** Balances valued below this USD amount collapse into the dust summary. */
export const DUST_THRESHOLD = '0.10';

export type QuantityDisplay = {
	/** Adaptive-precision text for the table cell. */
	text: string;
	/** Raw API value for the title/tooltip; disclosure for every rounding. */
	title: string;
	/** True when display differs from the raw string, so the cell can be marked. */
	compact: boolean;
};

function parseParts(amount: string): { negative: boolean; units: string; fraction: string } {
	const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
	if (!match) {
		throw new Error(`Invalid decimal string: ${amount}`);
	}
	return { negative: match[1] === '-', units: match[2], fraction: match[3] ?? '' };
}

/** Group the integer part of a decimal string; passes any fraction through. */
export function groupIntegerDigits(digits: string): string {
	const [integer, fraction] = digits.split('.');
	const sign = integer.startsWith('-') ? '-' : '';
	const body = sign ? integer.slice(1) : integer;
	const grouped = body.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
	return fraction === undefined ? `${sign}${grouped}` : `${sign}${grouped}.${fraction}`;
}

function trimTrailingZeros(fraction: string, minimum: number): string {
	let trimmed = fraction.replace(/0+$/, '');
	if (trimmed.length < minimum) {
		trimmed = fraction.slice(0, minimum).padEnd(minimum, '0');
	}
	return trimmed;
}

/**
 * Round-half-up the first `keep` fraction digits, matching the app's percent
 * rounding convention. Returns the kept digits plus a carry flag for the
 * 0.9999… case, where rounding overflows into the whole part.
 */
function roundFraction(fraction: string, keep: number): { text: string; carry: boolean } {
	if (fraction.length <= keep) {
		return { text: fraction.padEnd(keep, '0'), carry: false };
	}
	const kept = fraction.slice(0, keep);
	const rest = fraction.slice(keep);
	const increment = rest[0] >= '5' ? 1n : 0n;
	if (increment === 0n) {
		return { text: kept, carry: false };
	}
	const units = BigInt(kept) + increment;
	const text = units.toString().padStart(keep, '0');
	return { text, carry: units >= 10n ** BigInt(keep) };
}

/**
 * Adaptive-precision quantity display.
 *
 * Zero renders as `0`. Values ≥ 1 keep up to 4 fraction digits; values < 1
 * keep the first significant digit plus two more (bounded by the actual
 * precision). Every rounded cell carries the raw string in `title` so
 * nothing is hidden, only condensed.
 */
export function formatQuantityDisplay(quantity: string): QuantityDisplay {
	const raw = quantity.trim();
	const parts = parseParts(raw);
	const title = raw;
	const magnitude = BigInt(`${parts.units}${parts.fraction}`);
	if (magnitude === 0n) {
		return { text: '0', title, compact: raw !== '0' };
	}
	let fractionText: string;
	let carry: boolean;
	if (compareDecimalStrings(raw, '1') < 0) {
		const leadingZeros = parts.fraction.match(/^0*/)?.[0].length ?? 0;
		const keep = Math.min(leadingZeros + 3, parts.fraction.length);
		const rounded = roundFraction(parts.fraction, keep);
		fractionText = trimTrailingZeros(rounded.text, 2);
		carry = rounded.carry;
	} else {
		const rounded = roundFraction(parts.fraction, 4);
		fractionText = trimTrailingZeros(rounded.text, 2);
		carry = rounded.carry;
	}
	const whole = carry
		? (BigInt(parts.units) + 1n).toString()
		: parts.units.replace(/^0+(?=\d)/, '');
	const sign = parts.negative ? '-' : '';
	const grouped = groupIntegerDigits(`${sign}${whole}`);
	const text = parts.fraction === '' && !carry ? grouped : `${grouped}.${fractionText}`;
	return { text, title, compact: text !== raw };
}

export type DustAnalysis = {
	/** Valued rows at or above the threshold, in original order. */
	visible: PortfolioAsset[];
	/** Valued rows below the threshold, in original order. */
	dust: PortfolioAsset[];
	/** Exact USD total of the dust rows; null when there is no dust. */
	dustTotal: string | null;
};

/**
 * Split valued rows into visible and dust.
 *
 * Unvalued rows always stay visible — they are a disclosure, not dust — and
 * the dust total is an exact decimal sum, never float.
 */
export function analyzeDust(assets: readonly PortfolioAsset[]): DustAnalysis {
	const visible: PortfolioAsset[] = [];
	const dust: PortfolioAsset[] = [];
	for (const asset of assets) {
		if (asset.value !== null && compareDecimalStrings(asset.value.amount, DUST_THRESHOLD) < 0) {
			dust.push(asset);
		} else {
			visible.push(asset);
		}
	}
	if (dust.length === 0) {
		return { visible, dust, dustTotal: null };
	}
	return {
		visible,
		dust,
		dustTotal: normalizeSum(sumDecimalStrings(dust.map((a) => a.value?.amount ?? '0')))
	};
}

/** Trim trailing zeros from a sum's fraction so results stay canonical. */
function normalizeSum(amount: string): string {
	const [integer, fraction] = amount.split('.');
	if (fraction === undefined) {
		return amount;
	}
	const trimmed = fraction.replace(/0+$/, '');
	return trimmed.length === 0 ? integer : `${integer}.${trimmed}`;
}

/** Exact addition of canonical decimal strings at the maximal scale. */
export function sumDecimalStrings(amounts: readonly string[]): string {
	const parts = amounts.map((amount) => parseParts(amount));
	const scale = Math.max(0, ...parts.map((part) => part.fraction.length));
	let units = 0n;
	for (const part of parts) {
		const scaled =
			BigInt(`${part.negative ? '-' : ''}${part.units}${part.fraction}`) *
			10n ** BigInt(scale - part.fraction.length);
		units += scaled;
	}
	const negative = units < 0n;
	const digits = (negative ? -units : units).toString().padStart(scale + 1, '0');
	const splitAt = digits.length - scale;
	const joined = `${negative ? '-' : ''}${digits.slice(0, splitAt)}.${digits.slice(splitAt)}`;
	return normalizeSum(joined);
}

/**
 * Age of an ISO snapshot in whole minutes, floored at zero for clock skew.
 */
export function snapshotAgeMinutes(asOf: string, now: number = Date.now()): number | null {
	const parsed = Date.parse(asOf);
	if (Number.isNaN(parsed)) {
		return null;
	}
	return Math.max(0, Math.floor((now - parsed) / 60000));
}
