/**
 * Portfolio HTTP client: revision-guarded requests, structured `PortfolioApiError`s,
 * and readers for their stable codes and per-sleeve problems. Re-exported by `portfolios.ts`.
 */
import type { PortfolioFillComparisons } from './fill-comparison';
import { ensureBrowserCsrfSession, mutationHeaders } from './security';
import type {
	BacktestProblem,
	BacktestRunInput,
	JournalPage,
	Portfolio,
	PortfolioActionResponse,
	PortfolioBacktestAccepted,
	PortfolioBacktestDetail,
	PortfolioBacktestJob,
	PortfolioBacktestListing,
	PortfolioBacktestListResponse,
	PortfolioCreateInput,
	PortfolioDeployment,
	PortfolioListResponse,
	PortfolioUpdateInput,
	Proposal,
	ProposalListResponse,
	ProposalResponse,
	ProposalStatus,
	SetWeightsInput,
	SleeveAddInput
} from './portfolios-types';

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

/** The portfolio's deployment: state, each sleeve's bot, breakers, and exposure. */
export function fetchPortfolioDeployment(portfolioId: string): Promise<PortfolioDeployment> {
	return request<PortfolioDeployment>(portfolioPath(portfolioId, '/deployment'));
}

/** Paper vs live entry fills for this portfolio's sleeves that have a twin (ADR 0098). */
export function fetchFillComparisons(portfolioId: string): Promise<PortfolioFillComparisons> {
	return request<PortfolioFillComparisons>(portfolioPath(portfolioId, '/fill-comparisons'));
}

function sleeveSegment(sleeveId: string | null | undefined): string {
	return sleeveId ? `/sleeves/${encodeURIComponent(sleeveId)}` : '';
}

/** Start every sleeve (or one) at the reviewed revision; live needs the acknowledgement. */
export function startPortfolio(
	portfolioId: string,
	input: { revision: number; liveAcknowledged: boolean; sleeveId?: string | null }
): Promise<PortfolioActionResponse> {
	const body: Record<string, unknown> = { revision: input.revision };
	if (input.liveAcknowledged) body.i_understand_live = true;
	return request<PortfolioActionResponse>(
		portfolioPath(portfolioId, `${sleeveSegment(input.sleeveId)}/start`),
		{ method: 'POST', body: JSON.stringify(body) }
	);
}

/** Pause, resume, or stop every sleeve (or one); live resume needs the acknowledgement. */
export function portfolioAction(
	portfolioId: string,
	action: 'pause' | 'resume' | 'stop',
	input: { sleeveId?: string | null; flatten?: boolean; liveAcknowledged?: boolean } = {}
): Promise<PortfolioActionResponse> {
	const query = action === 'stop' && input.flatten ? '?flatten=true' : '';
	const body =
		action === 'resume' && input.liveAcknowledged
			? JSON.stringify({ i_understand_live: true })
			: undefined;
	return request<PortfolioActionResponse>(
		portfolioPath(portfolioId, `${sleeveSegment(input.sleeveId)}/${action}${query}`),
		{ method: 'POST', body }
	);
}

/** Clear a latched portfolio breaker; sleeves stay paused until resumed. */
export function resetPortfolioBreaker(portfolioId: string): Promise<PortfolioDeployment> {
	return request<PortfolioDeployment>(portfolioPath(portfolioId, '/breaker/reset'), {
		method: 'POST'
	});
}

/** The manager's proposals, newest first (pending first when filtered). */
export async function listProposals(
	portfolioId: string,
	status: ProposalStatus | null = null,
	limit = 20
): Promise<Proposal[]> {
	const params = new URLSearchParams({ limit: String(limit) });
	if (status !== null) params.set('status', status);
	const body = await request<ProposalListResponse>(
		portfolioPath(portfolioId, `/proposals?${params.toString()}`)
	);
	return body.proposals;
}

/** A person approves or declines one pending proposal. */
export function decideProposal(
	portfolioId: string,
	proposalId: string,
	decision: 'approve' | 'decline',
	input: { note?: string; liveAcknowledged?: boolean } = {}
): Promise<ProposalResponse> {
	const body: Record<string, unknown> = {};
	if (input.note) body.note = input.note;
	if (input.liveAcknowledged) body.i_understand_live = true;
	return request<ProposalResponse>(
		portfolioPath(portfolioId, `/proposals/${encodeURIComponent(proposalId)}/${decision}`),
		{ method: 'POST', body: JSON.stringify(body) }
	);
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
	return problemList(caught.detail.problems);
}

function problemList(raw: unknown): BacktestProblem[] {
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

/** Per-sleeve problems behind a 422 `portfolio_start_rejected`, else an empty list. */
export function startProblems(caught: unknown): BacktestProblem[] {
	if (!(caught instanceof PortfolioApiError) || caught.code !== 'portfolio_start_rejected') {
		return [];
	}
	return problemList(caught.detail.problems);
}
