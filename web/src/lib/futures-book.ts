/**
 * Paper futures books (ADR 0129 §4): payload types and the pure view model of
 * `GET /api/v1/deployments/{id}/futures` and the operator `futures-books` report.
 *
 * Every amount is USD (the CFM settlement currency) and is never added to a USDC or USDT
 * amount. `null` is unknown and renders "Unknown", never zero. Futures run as paper books
 * only: there is no live futures order path.
 */
import { formatUsd } from '$lib/portfolio';
import { formatUtcMinute } from '$lib/time';

export type FuturesEntryBlock =
	| 'FUTURES_CONTRACT_UNBOUND'
	| 'FUTURES_MARGIN_UNKNOWN'
	| 'FUNDING_HISTORY_MISSING'
	| 'FUTURES_POLICY_UNSET';

export type FuturesUnknownEvidence = 'binding' | 'mark' | 'margin_rates' | 'funding' | 'policy';

/** One funding hour applied to the book (USD; a negative `amount` was paid). */
export type FuturesFundingEntry = {
	funding_time: string;
	signed_quantity: string;
	mark_price: string;
	rate: string;
	amount: string;
};

export type FuturesBookPayload = {
	deployment_id: string;
	strategy_name: string | null;
	status: string;
	mode: 'paper' | 'live';
	product_id: string;
	currency: 'USD';
	contract_kind: 'perpetual_future' | 'dated_future' | null;
	underlying: string | null;
	contract_size: string | null;
	fee_per_contract: string | null;
	maker_fee_rate: string | null;
	taker_fee_rate: string | null;
	catalog_fingerprint: string | null;
	bound_at: string | null;
	side: 'long' | 'short' | 'flat';
	contracts: string | null;
	base_quantity: string;
	entry_price: string | null;
	mark_price: string | null;
	marked_at: string | null;
	paper_starting_cash: string | null;
	cash: string;
	equity: string | null;
	notional: string | null;
	leverage: string | null;
	policy_max_leverage: string | null;
	overnight_long_margin_rate: string | null;
	overnight_short_margin_rate: string | null;
	margin_observed_at: string | null;
	initial_margin: string | null;
	maintenance_margin: string | null;
	liquidation_buffer_fraction: string | null;
	min_liquidation_buffer_fraction: string | null;
	liquidation_price: string | null;
	funding_total: string;
	funding_hours: number;
	funding_overdue_since: string | null;
	recent_funding: FuturesFundingEntry[];
	daily_loss_latched: boolean;
	entry_blocks: FuturesEntryBlock[];
	unknown: FuturesUnknownEvidence[];
};

export type FuturesBooksReport = {
	report_kind: 'futures_books';
	overall_status: 'healthy' | 'degraded' | 'failed';
	components: { name: string; status: string; reason_code: string; detail: string }[];
	payload: {
		paper_capital_usd: string | null;
		committed_paper_cash_usd: string;
		futures_policy_set: boolean | null;
		books: FuturesBookPayload[];
		collateral_note: string;
		live_supported: false;
	};
};

export const UNKNOWN = 'Unknown';

export const PAPER_ONLY_NOTE =
	'Paper only: ThyTrader has no live futures order path. This book simulates fills, ' +
	'funding and liquidation against Coinbase marks and places no Coinbase orders.';

export const SHARED_COLLATERAL_NOTE =
	'In reality Coinbase counts your USDC spot balance as futures collateral, so a live ' +
	'futures position would share that money. Amounts here are USD and are never added to USDC.';

const ENTRY_BLOCK_TEXT: Record<FuturesEntryBlock, string> = {
	FUTURES_CONTRACT_UNBOUND: 'no contract is bound to this book',
	FUTURES_MARGIN_UNKNOWN: 'the overnight margin rates were never observed',
	FUNDING_HISTORY_MISSING: 'a funding hour is overdue',
	FUTURES_POLICY_UNSET: 'the futures risk policy is not set'
};

const UNKNOWN_TEXT: Record<FuturesUnknownEvidence, string> = {
	binding: 'contract binding',
	mark: 'mark price',
	margin_rates: 'margin rates',
	funding: 'funding history',
	policy: 'risk policy'
};

/** `$1,234.50`, `Unknown` for null, or the exact text with `USD` when it is not canonical. */
export function usdText(value: string | null): string {
	if (value === null) return UNKNOWN;
	try {
		return formatUsd(value);
	} catch {
		return `${value} USD`;
	}
}

/** Exact small USD amount (fees, funding hours) without rounding to cents. */
export function exactUsdText(value: string | null): string {
	return value === null ? UNKNOWN : `${value} USD`;
}

/** `25.00%` from a fraction (display only), or `Unknown`. */
export function percentText(fraction: string | null): string {
	if (fraction === null) return UNKNOWN;
	const number = Number(fraction);
	return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : fraction;
}

function timeText(iso: string | null): string {
	if (iso === null) return UNKNOWN;
	return Number.isFinite(Date.parse(iso)) ? formatUtcMinute(iso) : iso;
}

function leverageText(value: string | null): string {
	return value === null ? UNKNOWN : `${value}×`;
}

/** Human text for one entry-block reason code (unrecognised codes stay as the code). */
export function entryBlockText(code: string): string {
	const text = ENTRY_BLOCK_TEXT[code as FuturesEntryBlock];
	return text === undefined ? code : `${text} (${code})`;
}

/** "New entries denied: …", or null when nothing blocks the next entry. */
export function entryBlocksLine(blocks: readonly string[]): string | null {
	if (blocks.length === 0) return null;
	return `New entries denied: ${blocks.map(entryBlockText).join('; ')}. Exits are never blocked.`;
}

/** The unreadable evidence, stated as unknown rather than zero; null when all is known. */
export function unknownLine(unknown: readonly string[]): string | null {
	if (unknown.length === 0) return null;
	const labels = unknown.map((item) => UNKNOWN_TEXT[item as FuturesUnknownEvidence] ?? item);
	return (
		`Unknown, not zero: ${labels.join(', ')}. ` +
		'Figures that depend on them read Unknown and new entries are denied.'
	);
}

/** `Long 2 contracts`, `Short 1 contract`, `Flat`, or the side with unknown contracts. */
export function sideLine(book: Pick<FuturesBookPayload, 'side' | 'contracts'>): string {
	if (book.side === 'flat') return 'Flat';
	const side = book.side === 'long' ? 'Long' : 'Short';
	if (book.contracts === null) return `${side}, contracts unknown`;
	return `${side} ${book.contracts} contract${book.contracts === '1' ? '' : 's'}`;
}

export type FuturesFact = { id: string; label: string; value: string; hint: string | null };

export type FuturesFactGroup = { id: string; title: string; facts: FuturesFact[] };

export type FuturesFundingRow = {
	key: string;
	time: string;
	quantity: string;
	mark: string;
	rate: string;
	amount: string;
	tone: 'pos' | 'neg' | 'muted';
};

export type FuturesBookView = {
	groups: FuturesFactGroup[];
	fundingRows: FuturesFundingRow[];
	fundingOverdue: string | null;
	entryBlocks: string | null;
	unknown: string | null;
	latched: boolean;
};

function fact(id: string, label: string, value: string, hint: string | null = null): FuturesFact {
	return { id, label, value, hint };
}

function contractGroup(book: FuturesBookPayload): FuturesFactGroup {
	const kind =
		book.contract_kind === 'perpetual_future'
			? 'Perpetual'
			: book.contract_kind === 'dated_future'
				? 'Dated'
				: UNKNOWN;
	const size =
		book.contract_size === null
			? UNKNOWN
			: `${book.contract_size}${book.underlying ? ` ${book.underlying}` : ''}`;
	const rates =
		book.maker_fee_rate === null && book.taker_fee_rate === null
			? null
			: `maker ${book.maker_fee_rate ?? UNKNOWN} · taker ${book.taker_fee_rate ?? UNKNOWN}`;
	return {
		id: 'contract',
		title: 'Contract',
		facts: [
			fact('product', 'Product', book.product_id, kind),
			fact('underlying', 'Underlying', book.underlying ?? UNKNOWN),
			fact('contract-size', 'Contract size', size, 'Per contract'),
			fact('fee-per-contract', 'Fee per contract', exactUsdText(book.fee_per_contract), rates),
			fact('bound-at', 'Bound at', timeText(book.bound_at))
		]
	};
}

function positionGroup(book: FuturesBookPayload): FuturesFactGroup {
	const base = book.underlying ? `${book.base_quantity} ${book.underlying}` : book.base_quantity;
	return {
		id: 'position',
		title: 'Position',
		facts: [
			fact('side', 'Side', sideLine(book), book.side === 'flat' ? null : base),
			fact('entry', 'Entry price', book.side === 'flat' ? '—' : usdText(book.entry_price)),
			fact(
				'mark',
				'Mark price',
				usdText(book.mark_price),
				book.marked_at === null ? null : `at ${timeText(book.marked_at)}`
			),
			fact(
				'equity',
				'Equity',
				usdText(book.equity),
				book.paper_starting_cash === null ? null : `Started ${usdText(book.paper_starting_cash)}`
			),
			fact('cash', 'Cash', usdText(book.cash)),
			fact('notional', 'Notional', usdText(book.notional))
		]
	};
}

function riskGroup(book: FuturesBookPayload): FuturesFactGroup {
	const rates = `Overnight rate long ${percentText(book.overnight_long_margin_rate)} · short ${percentText(book.overnight_short_margin_rate)}`;
	return {
		id: 'risk',
		title: 'Margin and liquidation',
		facts: [
			fact(
				'leverage',
				'Leverage',
				leverageText(book.leverage),
				`Policy max ${leverageText(book.policy_max_leverage)}`
			),
			fact('initial-margin', 'Initial margin', usdText(book.initial_margin), rates),
			fact(
				'maintenance-margin',
				'Maintenance margin',
				usdText(book.maintenance_margin),
				'Equals initial (overnight)'
			),
			fact('margin-observed', 'Margin rates observed', timeText(book.margin_observed_at)),
			fact(
				'liquidation-buffer',
				'Liquidation buffer',
				percentText(book.liquidation_buffer_fraction),
				`Policy minimum ${percentText(book.min_liquidation_buffer_fraction)}`
			),
			fact(
				'liquidation-price',
				'Liquidation price',
				book.side === 'flat' ? '—' : usdText(book.liquidation_price),
				book.side === 'flat' ? 'No open position' : 'Equity would equal maintenance'
			)
		]
	};
}

function fundingGroup(book: FuturesBookPayload): FuturesFactGroup {
	return {
		id: 'funding',
		title: 'Funding',
		facts: [
			fact('funding-total', 'Funding total', usdText(book.funding_total), 'Negative was paid'),
			fact('funding-hours', 'Funding hours', String(book.funding_hours))
		]
	};
}

function fundingRow(entry: FuturesFundingEntry): FuturesFundingRow {
	const tone = entry.amount.startsWith('-')
		? 'neg'
		: /^[0.]+$/.test(entry.amount)
			? 'muted'
			: 'pos';
	return {
		key: entry.funding_time,
		time: timeText(entry.funding_time),
		quantity: entry.signed_quantity,
		mark: usdText(entry.mark_price),
		rate: entry.rate,
		amount: exactUsdText(entry.amount),
		tone
	};
}

/** The bot-detail card's view of one paper futures book. */
export function futuresBookView(book: FuturesBookPayload): FuturesBookView {
	return {
		groups: [contractGroup(book), positionGroup(book), riskGroup(book), fundingGroup(book)],
		fundingRows: book.recent_funding.map(fundingRow),
		fundingOverdue:
			book.funding_overdue_since === null
				? null
				: `A funding hour is overdue since ${timeText(book.funding_overdue_since)}.`,
		entryBlocks: entryBlocksLine(book.entry_blocks),
		unknown: unknownLine(book.unknown),
		latched: book.daily_loss_latched
	};
}

export type FuturesBookRow = {
	id: string;
	name: string;
	product: string;
	status: string;
	position: string;
	equity: string;
	buffer: string;
	warning: string | null;
};

export type FuturesBooksSection = {
	rows: FuturesBookRow[];
	committed: string;
	capital: string;
	collateralNote: string;
};

/** Short warning chip text for a book with entry blocks or unknown evidence. */
function rowWarning(book: FuturesBookPayload): string | null {
	if (book.unknown.length > 0) return 'Unknown evidence';
	if (book.entry_blocks.length > 0) return 'Entries denied';
	return null;
}

/** Home's "Paper futures books" rows: every book that is not stopped; null when none. */
export function futuresBooksSection(report: FuturesBooksReport): FuturesBooksSection | null {
	const rows = report.payload.books
		.filter((book) => book.status !== 'stopped')
		.map((book): FuturesBookRow => ({
			id: book.deployment_id,
			name: book.strategy_name ?? book.product_id,
			product: book.product_id,
			status: book.status,
			position: sideLine(book),
			equity: usdText(book.equity),
			buffer: percentText(book.liquidation_buffer_fraction),
			warning: rowWarning(book)
		}));
	if (rows.length === 0) return null;
	return {
		rows,
		committed: usdText(report.payload.committed_paper_cash_usd),
		capital: usdText(report.payload.paper_capital_usd),
		collateralNote: report.payload.collateral_note
	};
}
