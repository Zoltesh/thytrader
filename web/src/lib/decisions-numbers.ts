/** Exact-decimal compact display and the next-evaluation clock. Re-exported by `decisions.ts`. */
import type { Deployment } from './deployments';
import { timeframeMinutes } from './strategy-workspace';
import { formatUtcMinute } from './time';

// ---------------------------------------------------------------------------
// Numbers and clocks

const DECIMAL_TEXT = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;

/** Beyond this exponent a value is shown verbatim rather than expanded. */
const MAX_EXPANDED_EXPONENT = 64;

/**
 * Compact display of an exact decimal string (indicator values, closes).
 *
 * Keeps at least two fraction digits of precision and at least four
 * significant digits, rounds half up, then trims trailing zeros:
 * `47.2134` → `47.21`, `7.1234` → `7.123`, `0.000012345` → `0.00001235`,
 * `50.000` → `50`. Exponent forms (`1E-7`) expand. Null renders `—`; a value
 * that is not a decimal (`NaN`) is shown verbatim. Display only — never feed
 * the result back into arithmetic.
 */
export function formatDecimalCompact(value: string | null | undefined): string {
	if (value === null || value === undefined) return '—';
	const raw = value.trim();
	if (raw === '') return '—';
	const match = DECIMAL_TEXT.exec(raw);
	if (match === null) return raw;
	const whole = match[2] ?? '';
	const fraction = match[3] ?? '';
	if (whole === '' && fraction === '') return raw;
	const exponent = match[4] === undefined ? 0 : Number(match[4]);
	if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > MAX_EXPANDED_EXPONENT) return raw;

	// value = digits × 10^-scale, with no leading zeros in `digits`.
	let digits = `${whole}${fraction}`.replace(/^0+/, '');
	let scale = fraction.length - exponent;
	if (digits === '') return '0';
	if (scale < 0) {
		digits = `${digits}${'0'.repeat(-scale)}`;
		scale = 0;
	}

	// Fraction digits for four significant digits: `4 - integerDigits` covers both
	// sides of 1 (below 1, `-integerDigits` is the count of leading fraction zeros).
	const integerDigits = digits.length - scale;
	const keep = Math.max(2, 4 - integerDigits);
	if (scale > keep) {
		const drop = scale - keep;
		const kept = digits.slice(0, digits.length - drop);
		const roundUp = (digits[digits.length - drop] ?? '0') >= '5';
		digits = (BigInt(kept === '' ? '0' : kept) + (roundUp ? 1n : 0n)).toString();
		scale = keep;
	}

	const padded = digits.padStart(scale + 1, '0');
	const integerPart = padded.slice(0, padded.length - scale);
	const fractionPart = padded.slice(padded.length - scale).replace(/0+$/, '');
	const text = fractionPart === '' ? integerPart : `${integerPart}.${fractionPart}`;
	return match[1] === '-' && /[1-9]/.test(text) ? `-${text}` : text;
}

/**
 * When the next bar decision is due: the close of the bar after the last
 * evaluated one. `last_evaluated_bar` is a bar START, so this is
 * `last_evaluated_bar + 2 × timeframe`. Null when the bot has never evaluated
 * a bar or its clock cannot be parsed.
 */
export function nextEvaluationAt(
	deployment: Pick<Deployment, 'last_evaluated_bar' | 'timeframe'>
): Date | null {
	const bar = deployment.last_evaluated_bar;
	const timeframe = deployment.timeframe;
	if (bar === null || bar === undefined || timeframe === null || timeframe === undefined) {
		return null;
	}
	const minutes = timeframeMinutes(timeframe);
	if (minutes === null) return null;
	const startsAt = Date.parse(bar);
	if (Number.isNaN(startsAt)) return null;
	return new Date(startsAt + 2 * minutes * 60_000);
}

/**
 * Header line for when the bot next decides, approximately (workers evaluate
 * shortly after each close). Honest when unknown: before the first evaluation
 * it names the clock; a stopped bot is advanced only while residual exposure
 * remains; a deployment without a parseable clock shows `—`.
 */
export function nextEvaluationText(
	deployment: Pick<Deployment, 'last_evaluated_bar' | 'timeframe' | 'status'>
): string {
	const timeframe = deployment.timeframe;
	if (timeframe === null || timeframe === undefined || timeframeMinutes(timeframe) === null) {
		return 'Next evaluation: — (bar clock unknown)';
	}
	if (deployment.status === 'stopped') {
		return 'Stopped: bars are evaluated only while residual exposure remains';
	}
	const at = nextEvaluationAt(deployment);
	if (at === null) return `Next evaluation ≈ after the next ${timeframe} bar closes`;
	return `Next evaluation ≈ ${formatUtcMinute(at)}`;
}
