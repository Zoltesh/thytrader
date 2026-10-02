/**
 * Portfolios (ADR 0088): typed API client and pure view helpers.
 *
 * A portfolio is a set of sleeves (one strategy each, with a capital weight)
 * under shared limits, with manager settings and an append-only journal. Every
 * mutation is revision-guarded; a stale revision is a 409 the page answers by
 * reloading. Money and fractions are canonical decimal strings: sums and
 * comparisons stay exact (BigInt), and floats appear only in chart geometry.
 * Nothing here deploys a portfolio or places orders.
 */
import type { Deployment } from './deployments';
import { formatQuoteAmount } from './deployment-portfolio';
import { sumDecimalStrings } from './money';
import { compareDecimalStrings, formatPercent, subtractDecimalStrings } from './portfolio';
import { ensureBrowserCsrfSession, mutationHeaders } from './security';
import type { StrategyLibraryEntry } from './strategies';

export type PortfolioMode = 'paper' | 'live';
export type QuoteCurrency = 'USD' | 'USDC' | 'USDT';
export const QUOTE_CURRENCIES: readonly QuoteCurrency[] = ['USD', 'USDC', 'USDT'];
export type SleeveIssueCode = 'strategy_invalid' | 'quote_currency_mismatch' | 'product_unknown';

export type PortfolioLimits = {
	max_total_exposure_fraction: string;
	max_per_asset_fraction: string;
	daily_loss_quote: string | null;
	max_drawdown_fraction: string | null;
};

export type ManagerPermissions = {
	may_rebalance: boolean;
	max_weight_change_per_week: string;
	may_pause_sleeves: boolean;
	may_propose_sleeves: boolean;
};

export type ManagerSettings = {
	mandate: string;
	permissions: ManagerPermissions;
};

export type PortfolioSleeve = {
	sleeve_id: string;
	strategy_id: string;
	strategy_name: string;
	product_id: string | null;
	covered_product_ids: string[];
	timeframe: string | null;
	quote_currency: string | null;
	strategy_valid: boolean;
	current_fingerprint: string | null;
	weight_fraction: string;
	capital_quote: string;
	note: string | null;
	issues: SleeveIssueCode[];
	created_at: string;
	updated_at: string;
};

export type AssetAllocation = {
	asset: string;
	weight_fraction: string;
	sleeve_ids: string[];
};

export type PortfolioAllocation = {
	allocated_fraction: string;
	cash_reserve_fraction: string;
	unallocated_fraction: string;
	allocated_quote: string;
	cash_reserve_quote: string;
	unallocated_quote: string;
	assets: AssetAllocation[];
	largest_asset: AssetAllocation | null;
	largest_asset_within_limit: boolean | null;
};

export type Portfolio = {
	portfolio_id: string;
	name: string;
	mode: PortfolioMode;
	quote_currency: QuoteCurrency;
	capital_quote: string;
	cash_reserve_fraction: string;
	revision: number;
	created_at: string;
	updated_at: string;
	limits: PortfolioLimits;
	manager: ManagerSettings;
	sleeves: PortfolioSleeve[];
	allocation: PortfolioAllocation;
	deployable: false;
};

export type PortfolioListResponse = {
	portfolios: Portfolio[];
	limit: number;
	returned: number;
	total: number;
	has_more: boolean;
	next_cursor: string | null;
};

export type JournalKind =
	| 'created'
	| 'settings_changed'
	| 'sleeve_added'
	| 'sleeve_updated'
	| 'sleeve_removed'
	| 'weights_changed'
	| 'limits_changed'
	| 'manager_changed'
	| 'backtest_run';

export type JournalChange = { field: string; before: string | null; after: string | null };

export type JournalDetail = {
	sleeve_id?: string | null;
	strategy_id?: string | null;
	strategy_name?: string | null;
	reason?: 'operator' | 'strategy_deleted' | null;
	changes?: JournalChange[];
	job_id?: string | null;
	result_fingerprint?: string | null;
};

export type JournalEntry = {
	entry_id: string;
	portfolio_id: string;
	occurred_at: string;
	kind: JournalKind | string;
	actor: 'operator' | 'system' | 'manager' | string;
	channel: 'browser' | 'api' | 'system' | string;
	summary: string;
	detail: JournalDetail;
	revision: number;
};

export type JournalPage = {
	entries: JournalEntry[];
	limit: number;
	returned: number;
	total: number;
	has_more: boolean;
	next_cursor: string | null;
};

export type BacktestJobStatus =
	'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'expired';

export type PortfolioBacktestJob = {
	job_id: string;
	portfolio_id: string;
	portfolio_revision: number;
	status: BacktestJobStatus;
	created_at: string;
	updated_at: string;
	expires_at: string;
	evaluation_start: string;
	evaluation_end: string;
	sleeve_count: number;
	progress_current: number;
	progress_total: number;
	error_message: string | null;
	failed_detail: string | null;
	result_fingerprint: string | null;
};

export type PlannedSleeve = {
	sleeve_id: string;
	strategy_id: string;
	strategy_fingerprint: string;
	capital_quote: string;
	dataset_fingerprint: string;
};

export type PortfolioBacktestAccepted = {
	job: PortfolioBacktestJob;
	sleeves: PlannedSleeve[];
};

export type PortfolioBacktestListing = {
	result_fingerprint: string;
	portfolio_id: string;
	portfolio_revision: number;
	published_at: string;
	evaluation_start: string;
	evaluation_end: string;
	sleeve_count: number;
	total_return_fraction: string;
	maximum_drawdown_fraction: string;
	idle_capital_fraction: string;
	basket_total_return_fraction: string;
	trade_count: number;
};

export type PortfolioBacktestListResponse = {
	entries: PortfolioBacktestListing[];
	limit: number;
	offset: number;
	returned: number;
	has_more: boolean;
	next_cursor: string | null;
};

export type PortfolioBacktestCosts = {
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	spread_bps: string;
};

export type PortfolioSleeveResult = {
	sleeve_id: string;
	strategy_id: string;
	strategy_name: string;
	strategy_fingerprint: string;
	product_id: string;
	covered_product_ids: string[];
	timeframe: string;
	weight_fraction: string;
	capital_quote: string;
	run_fingerprint: string;
	result_fingerprint: string;
	dataset_fingerprint: string;
	final_equity: string;
	total_return_fraction: string;
	maximum_drawdown_fraction: string;
	trade_count: number;
	win_rate: string;
	contribution_fraction: string;
	correlation_to_rest: string | null;
	long_fraction: string | null;
};

export type PortfolioBacktestSummary = {
	initial_equity: string;
	final_equity: string;
	total_net_pnl: string;
	total_return_fraction: string;
	maximum_drawdown: string;
	maximum_drawdown_fraction: string;
	idle_capital_fraction: string;
	allocated_fraction: string;
	cash_quote: string;
	trade_count: number;
	best_sleeve_return_fraction: string;
	best_sleeve_maximum_drawdown_fraction: string;
	grid_points: number;
};

export type PortfolioCurveMetrics = {
	risk_free_rate: string;
	annualization: string;
	bar_seconds: string | null;
	bars_per_year: string | null;
	sharpe: string | null;
	sortino: string | null;
	calmar: string | null;
	cagr: string | null;
	annualized_volatility: string | null;
};

export type CorrelationPair = { sleeve_ids: [string, string]; coefficient: string | null };

export type PortfolioCorrelation = {
	return_clock_seconds: number;
	observations: number;
	pairs: CorrelationPair[];
};

export type OverlapPair = { sleeve_ids: [string, string]; asset: string; fraction: string };

export type PortfolioOverlap = {
	same_asset_fraction: string;
	long_together_fraction: string;
	pairs: OverlapPair[];
	excluded_sleeve_ids: string[];
};

export type BasketLeg = {
	product_id: string;
	dataset_fingerprint: string;
	timeframe: string;
	entry_price: string;
	exit_price: string;
	quantity: string;
	return_fraction: string;
};

export type PortfolioBasket = {
	legs: BasketLeg[];
	invested_quote: string;
	cash_quote: string;
	final_equity: string;
	total_return_fraction: string;
	maximum_drawdown_fraction: string;
	total_fees: string;
};

export type PortfolioEquityPoint = { at: string; equity: string; basket_equity: string };

export type PortfolioBacktestResult = {
	schema_version: string;
	contract: string;
	engine: string;
	portfolio_id: string;
	portfolio_revision: number;
	portfolio_name: string;
	mode: PortfolioMode;
	quote_currency: QuoteCurrency;
	capital_quote: string;
	cash_reserve_fraction: string;
	evaluation_start: string;
	evaluation_end: string;
	costs: PortfolioBacktestCosts;
	sleeves: PortfolioSleeveResult[];
	summary: PortfolioBacktestSummary;
	metrics: PortfolioCurveMetrics;
	correlation: PortfolioCorrelation;
	overlap: PortfolioOverlap;
	basket: PortfolioBasket;
	equity_curve: PortfolioEquityPoint[];
	disclosures: string[];
};

export type PortfolioBacktestDetail = {
	result_fingerprint: string;
	result: PortfolioBacktestResult;
	equity_curve_points: number;
	equity_curve_downsampled: boolean;
};

export type BacktestProblem = {
	code: string;
	message: string;
	sleeve_id: string | null;
	strategy_id: string | null;
	strategy_name: string | null;
};

export type PortfolioCreateInput = {
	name: string;
	mode: PortfolioMode;
	quote_currency: QuoteCurrency;
	capital_quote: string;
	cash_reserve_fraction: string;
};

export type PortfolioUpdateInput = {
	revision: number;
	name?: string;
	capital_quote?: string;
	cash_reserve_fraction?: string;
	limits?: PortfolioLimits;
	manager?: ManagerSettings;
};

export type SleeveAddInput = {
	revision: number;
	strategy_id: string;
	weight_fraction: string;
	note?: string;
};

export type SetWeightsInput = {
	revision: number;
	weights: { sleeve_id: string; weight_fraction: string }[];
	cash_reserve_fraction?: string;
};

export type BacktestRunInput = {
	revision: number;
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	spread_bps?: string;
};

export type PortfolioTab = 'sleeves' | 'backtest' | 'manager' | 'limits';
export const PORTFOLIO_TABS: readonly { id: PortfolioTab; label: string }[] = [
	{ id: 'sleeves', label: 'Sleeves' },
	{ id: 'backtest', label: 'Portfolio backtest' },
	{ id: 'manager', label: 'Manager' },
	{ id: 'limits', label: 'Limits' }
];

export const DEPLOY_ARRIVES_NEXT = 'Deploying a portfolio arrives next';
export const CONFLICT_RELOADED = 'Changed elsewhere; reloaded — try again.';
export const LIMITS_NOTE =
	'Stored with the portfolio. They start binding orders when portfolio deployment arrives; today no order passes through them, and portfolio backtests do not simulate them.';
export const MANAGER_NOTE =
	'The manager agent loop and approving or declining its proposals arrive next. Today these settings are saved and shown here; nothing acts on them.';
export const MANAGER_NEVER: readonly string[] = [
	'Place orders itself: strategies place every trade',
	'Raise limits or add sleeves directly (it can only propose a sleeve)',
	'Resume anything paused by you or a loss breaker'
];

/** Structured portfolio API failure: HTTP status, stable code, and the raw detail. */
export class PortfolioApiError extends Error {
	readonly status: number;
	readonly code: string | null;
	readonly detail: Record<string, unknown>;

	constructor(
		status: number,
		code: string | null,
		message: string,
		detail: Record<string, unknown>
	) {
		super(message);
		this.name = 'PortfolioApiError';
		this.status = status;
		this.code = code;
		this.detail = detail;
	}
}

function validationMessage(items: unknown[]): string {
	const messages = items
		.map((item) =>
			typeof item === 'object' && item !== null && 'msg' in item
				? String((item as { msg: unknown }).msg).replace(/^Value error, /, '')
				: null
		)
		.filter((message): message is string => message !== null && message !== '');
	return messages.length > 0 ? messages.join(' ') : 'The request was not valid.';
}

/** Read a FastAPI error body into a structured error (string, object, or validation list). */
export function portfolioApiError(status: number, body: unknown): PortfolioApiError {
	const detail =
		typeof body === 'object' && body !== null && 'detail' in body
			? (body as { detail: unknown }).detail
			: undefined;
	if (typeof detail === 'string') {
		return new PortfolioApiError(status, null, detail, {});
	}
	if (Array.isArray(detail)) {
		return new PortfolioApiError(status, 'request_invalid', validationMessage(detail), {});
	}
	if (typeof detail === 'object' && detail !== null) {
		const structured = detail as Record<string, unknown>;
		const code = typeof structured.code === 'string' ? structured.code : null;
		const message =
			typeof structured.message === 'string' && structured.message !== ''
				? structured.message
				: `The portfolio request failed (HTTP ${status}).`;
		return new PortfolioApiError(status, code, message, structured);
	}
	return new PortfolioApiError(status, null, `The portfolio request failed (HTTP ${status}).`, {});
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const method = init?.method?.toUpperCase() ?? 'GET';
	if (method !== 'GET' && method !== 'HEAD') {
		await ensureBrowserCsrfSession();
	}
	const response = await fetch(url, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...init?.headers,
			...mutationHeaders()
		}
	});
	if (!response.ok) {
		const body: unknown = await response.json().catch(() => ({}));
		throw portfolioApiError(response.status, body);
	}
	return (await response.json()) as T;
}

const BASE = '/api/v1/portfolios';

function portfolioPath(portfolioId: string, suffix = ''): string {
	return `${BASE}/${encodeURIComponent(portfolioId)}${suffix}`;
}

const MAX_PORTFOLIO_PAGES = 20;

/** Every portfolio, oldest first (follows pages; fails closed past the page cap). */
export async function listPortfolios(): Promise<Portfolio[]> {
	const rows: Portfolio[] = [];
	let cursor: string | null = null;
	for (let page = 0; page < MAX_PORTFOLIO_PAGES; page += 1) {
		const params = new URLSearchParams({ limit: '100' });
		if (cursor !== null) params.set('cursor', cursor);
		const body = await request<PortfolioListResponse>(`${BASE}?${params.toString()}`);
		rows.push(...body.portfolios);
		if (!body.has_more) return rows;
		if (body.next_cursor === null || body.portfolios.length === 0) {
			throw new Error('The portfolio list claimed more rows without a next page.');
		}
		cursor = body.next_cursor;
	}
	throw new Error('Portfolio list truncated: exceeded the page cap.');
}

export function fetchPortfolio(portfolioId: string): Promise<Portfolio> {
	return request<Portfolio>(portfolioPath(portfolioId));
}

export function createPortfolio(input: PortfolioCreateInput): Promise<Portfolio> {
	return request<Portfolio>(BASE, { method: 'POST', body: JSON.stringify(input) });
}

export function updatePortfolio(
	portfolioId: string,
	input: PortfolioUpdateInput
): Promise<Portfolio> {
	return request<Portfolio>(portfolioPath(portfolioId), {
		method: 'PATCH',
		body: JSON.stringify(input)
	});
}

export function addSleeve(portfolioId: string, input: SleeveAddInput): Promise<Portfolio> {
	return request<Portfolio>(portfolioPath(portfolioId, '/sleeves'), {
		method: 'POST',
		body: JSON.stringify(input)
	});
}

export function removeSleeve(
	portfolioId: string,
	sleeveId: string,
	revision: number
): Promise<Portfolio> {
	const query = new URLSearchParams({ revision: String(revision) });
	return request<Portfolio>(
		portfolioPath(portfolioId, `/sleeves/${encodeURIComponent(sleeveId)}?${query.toString()}`),
		{ method: 'DELETE' }
	);
}

export function setWeights(portfolioId: string, input: SetWeightsInput): Promise<Portfolio> {
	return request<Portfolio>(portfolioPath(portfolioId, '/weights'), {
		method: 'PUT',
		body: JSON.stringify(input)
	});
}

export function listJournal(
	portfolioId: string,
	cursor: string | null = null
): Promise<JournalPage> {
	const params = new URLSearchParams({ limit: '20' });
	if (cursor !== null) params.set('cursor', cursor);
	return request<JournalPage>(portfolioPath(portfolioId, `/journal?${params.toString()}`));
}

export function submitPortfolioBacktest(
	portfolioId: string,
	input: BacktestRunInput
): Promise<PortfolioBacktestAccepted> {
	return request<PortfolioBacktestAccepted>(portfolioPath(portfolioId, '/backtests'), {
		method: 'POST',
		body: JSON.stringify(input)
	});
}

export async function listPortfolioBacktests(
	portfolioId: string,
	limit = 10
): Promise<PortfolioBacktestListing[]> {
	const params = new URLSearchParams({ limit: String(limit) });
	const body = await request<PortfolioBacktestListResponse>(
		portfolioPath(portfolioId, `/backtests?${params.toString()}`)
	);
	return body.entries;
}

export async function listPortfolioBacktestJobs(
	portfolioId: string,
	limit = 5
): Promise<PortfolioBacktestJob[]> {
	const params = new URLSearchParams({ limit: String(limit) });
	const body = await request<{ jobs: PortfolioBacktestJob[] }>(
		portfolioPath(portfolioId, `/backtests/jobs?${params.toString()}`)
	);
	return body.jobs;
}

export function fetchPortfolioBacktestJob(
	portfolioId: string,
	jobId: string
): Promise<PortfolioBacktestJob> {
	return request<PortfolioBacktestJob>(
		portfolioPath(portfolioId, `/backtests/jobs/${encodeURIComponent(jobId)}`)
	);
}

export function fetchPortfolioBacktest(
	portfolioId: string,
	resultFingerprint: string,
	maxPoints = 600
): Promise<PortfolioBacktestDetail> {
	const params = new URLSearchParams({ max_points: String(maxPoints) });
	return request<PortfolioBacktestDetail>(
		portfolioPath(
			portfolioId,
			`/backtests/${encodeURIComponent(resultFingerprint)}?${params.toString()}`
		)
	);
}

/** The stable code of a caught portfolio failure, or null. */
export function portfolioErrorCode(caught: unknown): string | null {
	return caught instanceof PortfolioApiError ? caught.code : null;
}

export function isRevisionConflict(caught: unknown): boolean {
	return caught instanceof PortfolioApiError && caught.code === 'portfolio_revision_conflict';
}

export function isStorageUnavailable(caught: unknown): boolean {
	return caught instanceof PortfolioApiError && caught.status === 503;
}

export function errorText(caught: unknown, fallback: string): string {
	return caught instanceof Error && caught.message !== '' ? caught.message : fallback;
}

/** Per-sleeve problems behind a 422 `portfolio_backtest_rejected`, else an empty list. */
export function backtestProblems(caught: unknown): BacktestProblem[] {
	if (!(caught instanceof PortfolioApiError) || caught.code !== 'portfolio_backtest_rejected') {
		return [];
	}
	const raw = caught.detail.problems;
	if (!Array.isArray(raw)) return [];
	return raw.flatMap((item): BacktestProblem[] => {
		if (typeof item !== 'object' || item === null) return [];
		const record = item as Record<string, unknown>;
		const text = (key: string): string | null =>
			typeof record[key] === 'string' ? (record[key] as string) : null;
		return [
			{
				code: text('code') ?? 'unknown',
				message: text('message') ?? '',
				sleeve_id: text('sleeve_id'),
				strategy_id: text('strategy_id'),
				strategy_name: text('strategy_name')
			}
		];
	});
}

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

/** `0.3333` → `33.33%`, `0.5` → `50%` (exact; weights carry at most four places). */
export function weightPercent(fraction: string): string {
	return `${shiftDecimal(fraction, 2)}%`;
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

/** `1,234.50 USDC` (display rounding to cents; data stays exact). */
export function quoteText(amount: string, currency: string): string {
	return `${formatQuoteAmount(amount)} ${currency}`;
}

/** A signed fraction as a percent with two decimals (`+14.92%`, `-4.80%`). */
export function signedPercent(fraction: string): string {
	const text = formatPercent(fraction);
	return compareDecimalStrings(fraction, '0') > 0 ? `+${text}` : text;
}

/** A drawdown fraction (stored positive) shown as a loss (`-4.80%`, or `0.00%`). */
export function drawdownPercent(fraction: string): string {
	return compareDecimalStrings(fraction, '0') > 0 ? `-${formatPercent(fraction)}` : '0.00%';
}

/** Portfolio return points contributed by one sleeve (`+9.20 pts`). */
export function contributionPoints(fraction: string): string {
	return `${signedPercent(fraction).replace('%', '')} pts`;
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

/** A correlation coefficient with two decimals, or an em dash when undefined. */
export function coefficientText(value: string | null): string {
	return value === null ? '—' : roundDecimal(value, 2);
}

/** Sharpe and other ratios with two decimals, or an em dash when undefined. */
export function ratioText(value: string | null): string {
	return value === null ? '—' : roundDecimal(value, 2);
}

export function modeLabel(mode: PortfolioMode): 'LIVE' | 'Paper' {
	return mode === 'live' ? 'LIVE' : 'Paper';
}

/** Card subtitle: what the portfolio is, its quote, and who runs it. */
export function portfolioSubtitle(portfolio: Portfolio): string {
	const venue = portfolio.mode === 'live' ? 'Real Coinbase spot' : 'Simulated';
	const sleeves = portfolio.sleeves.length;
	return `${venue} · ${portfolio.quote_currency} · ${sleeves} sleeve${sleeves === 1 ? '' : 's'} · manager settings only`;
}

export type AllocationBar = {
	key: string;
	label: string;
	fraction: string;
	percent: string;
	/** CSS width (display only). */
	width: string;
	tone: 'sleeve' | 'reserve' | 'unallocated';
};

function cssWidth(fraction: string): string {
	const value = Number(shiftDecimal(fraction, 2));
	return `${Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0}%`;
}

/** One bar per sleeve, then the cash reserve, then unallocated cash when any. */
export function allocationBars(portfolio: Portfolio): AllocationBar[] {
	const bars: AllocationBar[] = portfolio.sleeves.map((sleeve) => ({
		key: sleeve.sleeve_id,
		label: `${sleeve.strategy_name}${sleeve.product_id ? ` · ${baseAsset(sleeve.product_id)}` : ''}`,
		fraction: sleeve.weight_fraction,
		percent: weightPercent(sleeve.weight_fraction),
		width: cssWidth(sleeve.weight_fraction),
		tone: 'sleeve'
	}));
	bars.push({
		key: 'reserve',
		label: 'Cash reserve',
		fraction: portfolio.allocation.cash_reserve_fraction,
		percent: weightPercent(portfolio.allocation.cash_reserve_fraction),
		width: cssWidth(portfolio.allocation.cash_reserve_fraction),
		tone: 'reserve'
	});
	if (compareDecimalStrings(portfolio.allocation.unallocated_fraction, '0') > 0) {
		bars.push({
			key: 'unallocated',
			label: 'Unallocated cash',
			fraction: portfolio.allocation.unallocated_fraction,
			percent: weightPercent(portfolio.allocation.unallocated_fraction),
			width: cssWidth(portfolio.allocation.unallocated_fraction),
			tone: 'unallocated'
		});
	}
	return bars;
}

export function allocationAriaLabel(bars: readonly AllocationBar[]): string {
	return `Allocation: ${bars.map((bar) => `${bar.label} ${bar.percent}`).join(', ')}`;
}

/** `BTC 50% (limit 60%)`, flagged when the largest asset is above the per-asset limit. */
export function largestAssetText(portfolio: Portfolio): { text: string; over: boolean } {
	const largest = portfolio.allocation.largest_asset;
	if (largest === null) return { text: 'No sleeves yet', over: false };
	const limit = weightPercent(portfolio.limits.max_per_asset_fraction);
	return {
		text: `${largest.asset} ${weightPercent(largest.weight_fraction)} (limit ${limit})`,
		over: portfolio.allocation.largest_asset_within_limit === false
	};
}

export function baseAsset(productId: string): string {
	const separator = productId.indexOf('-');
	return separator === -1 ? productId : productId.slice(0, separator);
}

const ISSUE_TEXT: Record<SleeveIssueCode, string> = {
	strategy_invalid: 'Rules invalid: fix the strategy before backtesting',
	quote_currency_mismatch: 'Quote currency changed: this sleeve no longer fits',
	product_unknown: 'No readable market: fix and save the strategy'
};

export function sleeveIssueText(code: SleeveIssueCode | string): string {
	return code in ISSUE_TEXT ? ISSUE_TEXT[code as SleeveIssueCode] : code;
}

const JOURNAL_KIND_LABELS: Record<JournalKind, string> = {
	created: 'Created',
	settings_changed: 'Settings changed',
	sleeve_added: 'Sleeve added',
	sleeve_updated: 'Sleeve updated',
	sleeve_removed: 'Sleeve removed',
	weights_changed: 'Weights changed',
	limits_changed: 'Limits changed',
	manager_changed: 'Manager settings changed',
	backtest_run: 'Backtest run'
};

export function journalKindLabel(kind: string): string {
	return kind in JOURNAL_KIND_LABELS ? JOURNAL_KIND_LABELS[kind as JournalKind] : kind;
}

/** Who acted: you in the browser, an operator through the API/CLI, the system, or the manager. */
export function journalActorText(entry: Pick<JournalEntry, 'actor' | 'channel'>): string {
	if (entry.actor === 'manager') return 'Manager agent';
	if (entry.actor === 'system') return 'System';
	return entry.channel === 'browser' ? 'You (browser)' : 'Operator (API or CLI)';
}

/** `2026-10-02 14:05 UTC`. */
export function utcMinute(iso: string): string {
	const parsed = Date.parse(iso);
	if (Number.isNaN(parsed)) return iso;
	return `${new Date(parsed).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

export function isJobActive(job: Pick<PortfolioBacktestJob, 'status'>): boolean {
	return job.status === 'queued' || job.status === 'running';
}

/** Progress copy for a job: queued, running sleeve N of M, combining, or its outcome. */
export function jobProgressText(job: PortfolioBacktestJob): string {
	switch (job.status) {
		case 'queued':
			return 'Queued…';
		case 'running':
			return job.progress_current < job.sleeve_count
				? `Running sleeve ${job.progress_current + 1} of ${job.sleeve_count}…`
				: 'Combining sleeves…';
		case 'completed':
			return 'Completed';
		case 'failed':
			return `Failed: ${job.error_message ?? 'no details returned'}`;
		case 'expired':
			return 'Expired before it finished. Run it again.';
		case 'cancelled':
			return 'Cancelled';
	}
}

/** A bar clock in seconds as `15m`, `4h`, or `1d`. */
export function durationText(seconds: number): string {
	if (seconds > 0 && seconds % 86_400 === 0) return `${seconds / 86_400}d`;
	if (seconds > 0 && seconds % 3_600 === 0) return `${seconds / 3_600}h`;
	return `${Math.max(1, Math.round(seconds / 60))}m`;
}

/** `2026-01-01 → 2026-09-30 · 272 days`. */
export function windowText(start: string, end: string): string {
	const startMs = Date.parse(start);
	const endMs = Date.parse(end);
	if (Number.isNaN(startMs) || Number.isNaN(endMs)) return `${start} → ${end}`;
	const days = Math.round((endMs - startMs) / 86_400_000);
	return `${start.slice(0, 10)} → ${end.slice(0, 10)} · ${days} day${days === 1 ? '' : 's'}`;
}

/** Coefficient of one sleeve pair regardless of order; undefined pairs read null. */
export function pairCoefficient(
	correlation: PortfolioCorrelation,
	left: string,
	right: string
): string | null {
	const pair = correlation.pairs.find(
		(item) =>
			(item.sleeve_ids[0] === left && item.sleeve_ids[1] === right) ||
			(item.sleeve_ids[0] === right && item.sleeve_ids[1] === left)
	);
	return pair?.coefficient ?? null;
}

/** The sleeve with the highest standalone return (first on ties). */
export function bestSleeve(result: PortfolioBacktestResult): PortfolioSleeveResult | null {
	let best: PortfolioSleeveResult | null = null;
	for (const sleeve of result.sleeves) {
		if (
			best === null ||
			compareDecimalStrings(sleeve.total_return_fraction, best.total_return_fraction) > 0
		) {
			best = sleeve;
		}
	}
	return best;
}

export type ChartPaths = {
	portfolio: string;
	basket: string;
	minAmount: string;
	maxAmount: string;
};

function geometryNumber(amount: string): number {
	const value = Number(amount);
	return Number.isFinite(value) ? value : 0;
}

/**
 * SVG paths for the combined and basket curves on one shared scale (display
 * geometry only: x by time, y by value; exact min/max stay strings).
 */
export function curvePaths(
	points: readonly PortfolioEquityPoint[],
	width: number,
	height: number,
	pad: number
): ChartPaths | null {
	if (points.length < 2) return null;
	const amounts = points.flatMap((point) => [point.equity, point.basket_equity]);
	let minAmount = amounts[0];
	let maxAmount = amounts[0];
	for (const amount of amounts) {
		if (compareDecimalStrings(amount, minAmount) < 0) minAmount = amount;
		if (compareDecimalStrings(amount, maxAmount) > 0) maxAmount = amount;
	}
	const minimum = geometryNumber(minAmount);
	const range = geometryNumber(maxAmount) - minimum || 1;
	const first = Date.parse(points[0].at);
	const span = Date.parse(points[points.length - 1].at) - first || 1;
	const path = (pick: (point: PortfolioEquityPoint) => string): string =>
		points
			.map((point, index) => {
				const x = ((Date.parse(point.at) - first) / span) * width;
				const y = pad + (1 - (geometryNumber(pick(point)) - minimum) / range) * (height - pad * 2);
				return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
			})
			.join(' ');
	return {
		portfolio: path((point) => point.equity),
		basket: path((point) => point.basket_equity),
		minAmount,
		maxAmount
	};
}

/** Running or paused bots (not stopped) of one strategy in the portfolio's mode. */
export function sleeveBots(
	inventory: readonly Deployment[] | null,
	strategyId: string,
	mode: PortfolioMode
): Deployment[] {
	if (inventory === null) return [];
	return inventory.filter(
		(deployment) =>
			deployment.strategy_id === strategyId &&
			deployment.mode === mode &&
			deployment.status !== 'stopped'
	);
}

export type PickerOption = {
	entry: StrategyLibraryEntry;
	quote: string | null;
	/** Why it cannot be added, or null when it can. */
	disabledReason: string | null;
	/** Shown even when it can be added (invalid rules still backtest-blocked). */
	warning: string | null;
};

/** Library strategies for the sleeve picker, filtered by name or market. */
export function pickerOptions(
	entries: readonly StrategyLibraryEntry[],
	portfolio: Portfolio,
	query: string
): PickerOption[] {
	const needle = query.trim().toLowerCase();
	const held = new Set(portfolio.sleeves.map((sleeve) => sleeve.strategy_id));
	return entries
		.filter(
			(entry) =>
				needle === '' ||
				entry.name.toLowerCase().includes(needle) ||
				(entry.product_id ?? '').toLowerCase().includes(needle)
		)
		.map((entry) => {
			const quote = entry.product_id === null ? null : quoteOf(entry.product_id);
			let disabledReason: string | null = null;
			if (held.has(entry.strategy_id)) disabledReason = 'Already a sleeve';
			else if (quote === null) disabledReason = 'No readable market yet';
			else if (quote !== portfolio.quote_currency)
				disabledReason = `Trades in ${quote}; this portfolio holds ${portfolio.quote_currency}`;
			return {
				entry,
				quote,
				disabledReason,
				warning: entry.valid ? null : 'Rules invalid: fix before backtesting'
			};
		});
}

function quoteOf(productId: string): string | null {
	const separator = productId.indexOf('-');
	return separator === -1 ? null : productId.slice(separator + 1);
}

/** How much weight still fits beside the current sleeves and reserve (never negative). */
export function remainingWeight(portfolio: Portfolio): string {
	const check = checkAllocation(
		portfolio.sleeves.map((sleeve) => sleeve.weight_fraction),
		portfolio.cash_reserve_fraction
	);
	return compareDecimalStrings(check.remaining, '0') > 0 ? check.remaining : '0';
}

export function parseTab(value: string | null): PortfolioTab {
	return PORTFOLIO_TABS.some((tab) => tab.id === value) ? (value as PortfolioTab) : 'sleeves';
}
