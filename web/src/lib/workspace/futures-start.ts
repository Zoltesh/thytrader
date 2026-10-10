/**
 * Pure helpers of the Run stage paper futures start (ADR 0129): required fields,
 * prefill from Coinbase futures fee evidence (ADR 0128), and the evidence source line.
 * No default fee is ever invented: a field without evidence stays blank and required.
 */
import type { FuturesFeeEvidence } from '$lib/futures-book-api';

export type FuturesStartFields = {
	/** Paper starting cash, USD. */
	cash: string;
	maker: string;
	taker: string;
	/** Fee per contract, USD. */
	perContract: string;
};

const LABELS: Record<keyof FuturesStartFields, string> = {
	cash: 'starting cash (USD)',
	maker: 'maker fee rate',
	taker: 'taker fee rate',
	perContract: 'fee per contract (USD)'
};

const DECIMAL = /^\d+(?:\.\d+)?$/;

/** Labels of the fields that are blank or not a non-negative decimal, in form order. */
export function futuresStartMissing(fields: FuturesStartFields): string[] {
	return (Object.keys(LABELS) as (keyof FuturesStartFields)[])
		.filter((key) => !DECIMAL.test(fields[key].trim()))
		.map((key) => LABELS[key]);
}

function fill(current: string, evidence: string | null, overwrite: boolean): string {
	if (evidence === null) return current;
	return overwrite || current.trim() === '' ? evidence : current;
}

/**
 * Prefill blank fee fields from reported futures fee evidence; typed values stay.
 * `quoted` is an explicit per-contract quote the operator asked for, which replaces
 * the per-contract field.
 */
export function prefillFuturesFees(
	fields: FuturesStartFields,
	evidence: FuturesFeeEvidence | null,
	quoted = false
): FuturesStartFields {
	if (evidence === null) return fields;
	const available = evidence.status === 'available';
	return {
		cash: fields.cash,
		maker: available ? fill(fields.maker, evidence.maker_fee_rate, false) : fields.maker,
		taker: available ? fill(fields.taker, evidence.taker_fee_rate, false) : fields.taker,
		perContract: fill(fields.perContract, evidence.fee_per_contract, quoted)
	};
}

/** Where the fee fields came from, or why they are blank. */
export function futuresFeeSourceText(
	evidence: FuturesFeeEvidence | null,
	state: 'loading' | 'ready' | 'error'
): string {
	if (state === 'loading') return 'Reading your Coinbase futures fee rates…';
	if (state === 'error')
		return 'Futures fee evidence could not be read; enter the rates you expect.';
	if (evidence === null || evidence.status !== 'available') {
		const reason = evidence?.unavailable_reason ? ` (${evidence.unavailable_reason})` : '';
		return `Coinbase reported no futures fee rates${reason}; enter the rates you expect.`;
	}
	const tier = evidence.fee_tier ? ` (${evidence.fee_tier})` : '';
	const perContract =
		evidence.fee_per_contract === null
			? 'Coinbase reports no per-contract fee without an order preview; enter it or quote it.'
			: `Fee per contract quoted by a Coinbase order preview of ${evidence.preview_product_id ?? 'the contract'} (no order placed).`;
	return `Maker and taker from your Coinbase futures fee tier${tier}. ${perContract}`;
}
