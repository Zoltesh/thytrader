/**
 * HTTP reads for paper futures books (ADR 0129) and the futures fee evidence that
 * prefills a paper futures start (ADR 0128). Every amount is USD; `null` is unknown.
 */
import { request } from './deployments-http';
import type { FuturesBookPayload, FuturesBooksReport } from './futures-book';

/** `payload.futures` of the operator `fees` report: the separate futures fee evidence. */
export type FuturesFeeEvidence = {
	status: 'available' | 'unavailable';
	maker_fee_rate: string | null;
	taker_fee_rate: string | null;
	fee_tier: string | null;
	as_of: string | null;
	/** The fixed per-contract part only; the maker/taker rate is charged on top (ADR 0133). */
	fee_per_contract: string | null;
	fee_per_contract_source:
		'operator_input' | 'orders_preview_itemized' | 'orders_preview_less_taker_rate';
	fee_per_contract_unavailable_reason: string | null;
	unavailable_reason: 'unsupported' | 'read_failed' | null;
	preview_product_id: string | null;
	preview_unavailable_reason: string | null;
};

/** One paper futures bot's book; the API answers 404 for a spot bot. */
export function fetchDeploymentFutures(id: string): Promise<FuturesBookPayload> {
	return request<FuturesBookPayload>(`/api/v1/deployments/${encodeURIComponent(id)}/futures`);
}

/** Every paper futures book plus the policy envelope that funds them. */
export function fetchFuturesBooks(): Promise<FuturesBooksReport> {
	return request<FuturesBooksReport>('/api/v1/operator/futures-books');
}

export type FuturesRestartFees = {
	maker_fee_rate: string;
	taker_fee_rate: string;
	paper_fee_per_contract: string;
};

/**
 * The fees a restarted paper futures book keeps (a futures start requires all three).
 * Throws before anything is stopped when the book's fees are unknown.
 */
export async function futuresRestartFees(id: string): Promise<FuturesRestartFees> {
	const book = await fetchDeploymentFutures(id);
	if (
		book.maker_fee_rate === null ||
		book.taker_fee_rate === null ||
		book.fee_per_contract === null
	)
		throw new Error(
			"This futures book's fees are unknown, so a new start would be refused. Start it from Run with explicit fees."
		);
	return {
		maker_fee_rate: book.maker_fee_rate,
		taker_fee_rate: book.taker_fee_rate,
		paper_fee_per_contract: book.fee_per_contract
	};
}

/**
 * Futures fee evidence, or null when the report carries none.
 *
 * `previewProductId` opts into the server's single Coinbase `orders/preview` for one
 * contract (it places no order) so the report can quote the per-contract fee.
 */
export async function fetchFuturesFeeEvidence(
	previewProductId?: string
): Promise<FuturesFeeEvidence | null> {
	const query =
		previewProductId === undefined
			? ''
			: `?${new URLSearchParams({ futures_preview_product_id: previewProductId })}`;
	const report = await request<{ payload: { futures?: FuturesFeeEvidence | null } }>(
		`/api/v1/operator/fees${query}`
	);
	return report.payload.futures ?? null;
}
