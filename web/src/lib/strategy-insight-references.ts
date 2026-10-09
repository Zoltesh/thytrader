/**
 * Reference-instrument checks for the strategy builder (ADR 0096), mirrored from
 * the backend. Re-exported by `strategy-insight.ts`.
 */
import {
	MAX_REFERENCE_INSTRUMENTS,
	quoteCurrencyFor,
	validReferenceTimeframes,
	type BuilderModel,
	type ReferenceInstrumentDraft
} from './strategies';

const REFERENCE_ID_PATTERN = /^[a-z][a-z0-9_]{0,31}$/;
const SPOT_PRODUCT_PATTERN = /^[A-Z0-9]{2,20}-(?:USD|USDC|USDT)$/;

/**
 * Reference-instrument rules mirrored from the backend (ADR 0096): at most three, unique
 * ids and series, the strategy's quote currency, the decision clock or a coarser integer
 * multiple, every reference read by an indicator, and every `source` declared.
 */
export function validateReferenceInstruments(model: BuilderModel): string[] {
	const problems: string[] = [];
	const references: ReferenceInstrumentDraft[] = model.reference_instruments;
	if (references.length > MAX_REFERENCE_INSTRUMENTS) {
		problems.push(`At most ${MAX_REFERENCE_INSTRUMENTS} reference instruments are allowed.`);
	}
	const ids = new Set(references.map((reference) => reference.id));
	if (ids.size !== references.length) problems.push('Reference instrument ids must be unique.');
	const series = new Set(
		references.map((reference) => `${reference.product_id}:${reference.timeframe}`)
	);
	if (series.size !== references.length) {
		problems.push('Reference instruments must not repeat one product and timeframe.');
	}
	const quote = quoteCurrencyFor(model.product_id, '');
	for (const reference of references) {
		const label = `Reference instrument "${reference.id}"`;
		if (!REFERENCE_ID_PATTERN.test(reference.id)) {
			problems.push(
				`${label} id must start with a lowercase letter and use lowercase letters, digits, or underscores (at most 32).`
			);
		}
		if (!SPOT_PRODUCT_PATTERN.test(reference.product_id)) {
			problems.push(`${label} product must be a BASE-USD, BASE-USDC, or BASE-USDT spot product.`);
		} else if (quote !== '' && quoteCurrencyFor(reference.product_id) !== quote) {
			problems.push(`${label} must use the strategy quote currency ${quote}.`);
		}
		if (!validReferenceTimeframes(model.timeframe).includes(reference.timeframe)) {
			problems.push(
				`${label} timeframe ${reference.timeframe} must equal ${model.timeframe} or be a coarser integer multiple of it.`
			);
		}
		if (!model.indicators.some((indicator) => indicator.source === reference.id)) {
			problems.push(
				`${label} must be read by at least one indicator (pick it as an indicator's instrument).`
			);
		}
	}
	for (const indicator of model.indicators) {
		if (indicator.source === undefined || indicator.source === '') continue;
		if (!ids.has(indicator.source)) {
			problems.push(
				`Indicator "${indicator.id}" reads unknown reference instrument "${indicator.source}".`
			);
		}
		if (indicator.kind === 'constant') {
			problems.push(`Indicator "${indicator.id}" constant must omit the reference instrument.`);
		}
		if (indicator.timeframe !== undefined && indicator.timeframe !== '') {
			problems.push(
				`Indicator "${indicator.id}" reads a reference instrument, so it must omit its own timeframe.`
			);
		}
	}
	return problems;
}
