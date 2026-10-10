/**
 * Futures fields of the strategy builder (ADR 0128): `instrument.kind` and the
 * `derivatives` block. A spot strategy carries neither, so its saved document keeps
 * the bytes it had before futures existed; a futures strategy must round-trip both.
 */

/** `instrument.kind`: absent on spot documents, `future` on a CFM contract. */
export type InstrumentKind = 'spot' | 'future';

/** The `derivatives` block of a futures strategy, as the form edits it. */
export type DerivativesDraft = {
	/** The strategy's own leverage ceiling, 1 to 20 (the risk policy's ceiling applies too). */
	max_leverage: string;
	/** P1 sizes and admits on overnight margin rates only. */
	margin_mode: 'overnight';
	/** Hours before a dated contract's expiry to flatten; null when the document omits it. */
	flatten_before_expiry_hours: number | null;
};

/** Leverage bounds the backend enforces on `derivatives.max_leverage`. */
export const MIN_STRATEGY_LEVERAGE = 1;
export const MAX_STRATEGY_LEVERAGE = 20;

/** Futures settle in USD: the quote of every CFM contract (ADR 0128). */
export const FUTURES_QUOTE_CURRENCY = 'USD';

/** The kind a saved `instrument` declares: `future` only when it says so. */
export function instrumentKindOf(instrument: unknown): InstrumentKind {
	const kind = (instrument as { kind?: unknown } | null | undefined)?.kind;
	return kind === 'future' ? 'future' : 'spot';
}

/** Draft of a saved `derivatives` block, or null when the document has none. */
export function toDerivativesDraft(raw: unknown): DerivativesDraft | null {
	if (raw === null || raw === undefined || typeof raw !== 'object') return null;
	const block = raw as {
		max_leverage?: unknown;
		flatten_before_expiry_hours?: unknown;
	};
	const hours = block.flatten_before_expiry_hours;
	return {
		max_leverage: typeof block.max_leverage === 'string' ? block.max_leverage : '',
		margin_mode: 'overnight',
		flatten_before_expiry_hours: typeof hours === 'number' ? hours : null
	};
}

/** A new futures block: unlevered, overnight margin, no expiry flatten. */
export function defaultDerivatives(): DerivativesDraft {
	return { max_leverage: '1', margin_mode: 'overnight', flatten_before_expiry_hours: null };
}

/**
 * The `derivatives` document block. `flatten_before_expiry_hours` is written only when
 * set, matching the backend's canonical form (it omits a null value).
 */
export function serializeDerivatives(draft: DerivativesDraft): {
	max_leverage: string;
	margin_mode: 'overnight';
	flatten_before_expiry_hours?: number;
} {
	return {
		max_leverage: draft.max_leverage.trim(),
		margin_mode: draft.margin_mode,
		...(draft.flatten_before_expiry_hours === null
			? {}
			: { flatten_before_expiry_hours: draft.flatten_before_expiry_hours })
	};
}

const DECIMAL = /^\d+(?:\.\d+)?$/;

/** Problems with the futures fields the form can catch before the server does. */
export function validateDerivatives(
	kind: InstrumentKind,
	derivatives: DerivativesDraft | null,
	productIdIsFutures: boolean
): string[] {
	if (kind !== 'future') return [];
	const problems: string[] = [];
	if (!productIdIsFutures) {
		problems.push('A futures strategy needs a CFM contract id such as ETP-20DEC30-CDE.');
	}
	if (derivatives === null) {
		problems.push('A futures strategy needs a maximum leverage.');
		return problems;
	}
	const leverage = derivatives.max_leverage.trim();
	if (
		!DECIMAL.test(leverage) ||
		Number(leverage) < MIN_STRATEGY_LEVERAGE ||
		Number(leverage) > MAX_STRATEGY_LEVERAGE
	) {
		problems.push(
			`Maximum leverage must be a decimal between ${MIN_STRATEGY_LEVERAGE} and ${MAX_STRATEGY_LEVERAGE}.`
		);
	}
	return problems;
}
