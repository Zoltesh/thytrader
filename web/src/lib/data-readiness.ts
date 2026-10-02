/**
 * Which product × timeframe datasets a strategy needs, and the confirmation-gated
 * "Download data" flow that asks the market-data worker for them.
 *
 * The flow uses only the existing data-lane HTTP endpoints (the same ones
 * `thytrader-data watch-add` / `ingest --no-wait` call):
 *
 * - `GET  /api/v1/data/watchlist` to keep an existing longer lookback,
 * - `PUT  /api/v1/data/watchlist` (watch-add),
 * - `POST /api/v1/data/ingest` (202, no wait), and
 * - `GET  /api/v1/data/ingest?product_id=&timeframe=` to poll progress.
 *
 * Coinbase returns no candle for an interval without trades; the worker publishes a
 * flat zero-volume bar for each confirmed one (ADR 0095). A dataset stays shorter than
 * the watch window only when Coinbase has no trades at all before it (the listing,
 * reported as `history_floor_at`).
 */
import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';
import { extraIndicatorTimeframes, type BuilderModel, type Dataset } from '$lib/strategies';
import { timeframeMinutes } from '$lib/strategy-workspace';

/** Why a strategy needs one clock. */
export type ClockRole = 'execution' | 'htf' | 'indicator' | 'reference';

export type RequiredClock = {
	productId: string;
	timeframe: string;
	role: ClockRole;
};

/** `missing`: no verified dataset; `stale`: the newest one ends well before now. */
export type ClockAvailability = 'ready' | 'stale' | 'missing';

export type ClockReadiness = RequiredClock & {
	availability: ClockAvailability;
	dataset: Dataset | null;
};

/** Worker coverage as `worker_state_payload` serializes it (fields are optional). */
export type IngestWorkerState = {
	status: string;
	watch_complete?: boolean | null;
	complete?: boolean;
	watch_expected_candle_count?: number | null;
	failure_code?: string | null;
	covered_starts_at?: string | null;
	covered_ends_at?: string | null;
	expected_candle_count?: number | null;
	received_candle_count?: number | null;
	/** Bars of the watch lookback the verified series covers (ADR 0095). */
	watch_covered_candle_count?: number | null;
	/** Coverage legitimately starts here: Coinbase has no trades before it (the listing). */
	history_floor_at?: string | null;
};

export type IngestStatus = {
	product_id: string;
	timeframe: string;
	ingest_requested_at: string | null;
	state: IngestWorkerState;
};

type WatchTarget = {
	product_id: string;
	timeframe: string;
	lookback_hours: number;
	enabled: boolean;
};

const HOURS_PER_DAY = 24;
const HOURS_PER_YEAR = 365 * HOURS_PER_DAY;

/**
 * Per-timeframe watch lookback ceilings the data API enforces (ADR 0085): 1m 90 days,
 * 5m 1 year, 15m 2 years, 30m 3 years, 1h 5 years, 2h-1d 10 years. A market listed
 * later has less history; the worker then reports its listing as `history_floor_at`.
 */
export const WATCH_LOOKBACK_CEILING_HOURS: Readonly<Record<string, number>> = {
	'1m': 90 * HOURS_PER_DAY,
	'5m': HOURS_PER_YEAR,
	'15m': 2 * HOURS_PER_YEAR,
	'30m': 3 * HOURS_PER_YEAR,
	'1h': 5 * HOURS_PER_YEAR,
	'2h': 10 * HOURS_PER_YEAR,
	'4h': 10 * HOURS_PER_YEAR,
	'6h': 10 * HOURS_PER_YEAR,
	'1d': 10 * HOURS_PER_YEAR
};

/** The most conservative ceiling, for a clock the table does not list. */
const FALLBACK_WATCH_LOOKBACK_HOURS = 90 * HOURS_PER_DAY;

/** A dataset is stale when it ends more than this many bars (and at least 2h) before now. */
const STALE_BARS = 3;
const STALE_MINIMUM_MINUTES = 120;

export class DataLaneError extends Error {
	readonly status: number;

	constructor(status: number, message: string) {
		super(message);
		this.name = 'DataLaneError';
		this.status = status;
	}
}

/**
 * Every clock a strategy reads: its execution clock, the optional HTF filter
 * clock, extra per-indicator clocks, and each read-only reference instrument
 * series (ADR 0096), in that order and without duplicates.
 */
export function requiredClocks(model: BuilderModel): RequiredClock[] {
	const clocks: RequiredClock[] = [
		{ productId: model.product_id, timeframe: model.timeframe, role: 'execution' }
	];
	const seen = new Set([model.timeframe]);
	const htf = model.htf_filter?.timeframe;
	if (htf !== undefined && !seen.has(htf)) {
		clocks.push({ productId: model.product_id, timeframe: htf, role: 'htf' });
		seen.add(htf);
	}
	for (const timeframe of extraIndicatorTimeframes(model.indicators, model.timeframe)) {
		if (seen.has(timeframe)) continue;
		clocks.push({ productId: model.product_id, timeframe, role: 'indicator' });
		seen.add(timeframe);
	}
	const series = new Set(clocks.map((clock) => `${clock.productId}:${clock.timeframe}`));
	for (const reference of model.reference_instruments) {
		const key = `${reference.product_id}:${reference.timeframe}`;
		if (series.has(key)) continue;
		clocks.push({
			productId: reference.product_id,
			timeframe: reference.timeframe,
			role: 'reference'
		});
		series.add(key);
	}
	return clocks;
}

/** Classify one clock against the latest verified datasets. */
export function clockReadiness(
	clock: RequiredClock,
	datasets: Dataset[],
	now: Date = new Date()
): ClockReadiness {
	const matching = datasets
		.filter(
			(dataset) => dataset.product_id === clock.productId && dataset.timeframe === clock.timeframe
		)
		.sort((left, right) => Date.parse(right.ends_at) - Date.parse(left.ends_at));
	const dataset = matching[0] ?? null;
	if (dataset === null) return { ...clock, availability: 'missing', dataset: null };
	return {
		...clock,
		availability: isStale(dataset, clock.timeframe, now) ? 'stale' : 'ready',
		dataset
	};
}

/** Readiness of every clock the strategy needs. */
export function strategyReadiness(
	model: BuilderModel,
	datasets: Dataset[],
	now: Date = new Date()
): ClockReadiness[] {
	return requiredClocks(model).map((clock) => clockReadiness(clock, datasets, now));
}

function isStale(dataset: Dataset, timeframe: string, now: Date): boolean {
	const ends = Date.parse(dataset.ends_at);
	if (Number.isNaN(ends)) return true;
	const minutes = timeframeMinutes(timeframe) ?? 60;
	const allowedMinutes = Math.max(minutes * STALE_BARS, STALE_MINIMUM_MINUTES);
	return now.getTime() - ends > allowedMinutes * 60_000;
}

/** Human label for one clock, for example `BTC-USDC 2h (HTF filter)`. */
export function clockLabel(clock: RequiredClock): string {
	const role =
		clock.role === 'execution'
			? 'execution clock'
			: clock.role === 'htf'
				? 'HTF filter'
				: clock.role === 'reference'
					? 'reference instrument'
					: 'indicator clock';
	return `${clock.productId} ${clock.timeframe} (${role})`;
}

/** One sentence naming exactly what is missing or stale for a clock. */
export function readinessMessage(readiness: ClockReadiness): string | null {
	const subject = `${readiness.productId} × ${readiness.timeframe}`;
	const role =
		readiness.role === 'execution'
			? 'execution'
			: readiness.role === 'htf'
				? 'higher-timeframe filter'
				: readiness.role === 'reference'
					? 'reference instrument'
					: 'extra indicator';
	if (readiness.availability === 'missing') {
		return `No verified ${subject} dataset yet. This strategy's ${role} clock needs it.`;
	}
	if (readiness.availability === 'stale' && readiness.dataset !== null) {
		return `The verified ${subject} dataset ends ${formatUtc(readiness.dataset.ends_at)}, so it is stale. This strategy's ${role} clock reads it.`;
	}
	return null;
}

/** Default watch lookback for a clock (the per-timeframe ceiling the API enforces). */
export function defaultWatchLookbackHours(timeframe: string): number {
	return WATCH_LOOKBACK_CEILING_HOURS[timeframe] ?? FALLBACK_WATCH_LOOKBACK_HOURS;
}

/** Spell a lookback in whole years or days when it divides evenly, for dialog copy. */
export function describeLookbackHours(hours: number): string {
	if (hours % HOURS_PER_YEAR === 0) {
		const years = hours / HOURS_PER_YEAR;
		return years === 1 ? '1 year' : `${years} years`;
	}
	if (hours % HOURS_PER_DAY === 0) {
		const days = hours / HOURS_PER_DAY;
		return days === 1 ? '1 day' : `${days} days`;
	}
	return `${hours} hours`;
}

/**
 * Watch the clock (keeping any longer existing lookback) and queue a no-wait
 * ingest. Returns the 202 ingest state.
 */
export async function requestDatasetDownload(
	productId: string,
	timeframe: string
): Promise<IngestStatus> {
	const existing = (
		await dataRequest<{ targets: WatchTarget[] }>('/api/v1/data/watchlist')
	).targets.find((target) => target.product_id === productId && target.timeframe === timeframe);
	const lookback = Math.max(existing?.lookback_hours ?? 0, defaultWatchLookbackHours(timeframe));
	if (existing === undefined || !existing.enabled || existing.lookback_hours < lookback) {
		await dataRequest('/api/v1/data/watchlist', {
			method: 'PUT',
			body: JSON.stringify({
				product_id: productId,
				timeframe,
				lookback_hours: lookback,
				enabled: true
			})
		});
	}
	const accepted = await dataRequest<IngestStatus & { accepted: boolean }>('/api/v1/data/ingest', {
		method: 'POST',
		body: JSON.stringify({ product_id: productId, timeframe })
	});
	return accepted;
}

/** Poll one clock's ingest state. */
export async function fetchIngestStatus(
	productId: string,
	timeframe: string
): Promise<IngestStatus> {
	const params = new URLSearchParams({ product_id: productId, timeframe });
	return dataRequest<IngestStatus>(`/api/v1/data/ingest?${params.toString()}`);
}

export type DownloadProgress = {
	/** True once the worker reports the watch window covered. */
	done: boolean;
	/** True when the worker reported a failure code for its latest attempt. */
	failed: boolean;
	received: number | null;
	expected: number | null;
	watchStatus: 'complete' | 'backfilling' | 'waiting';
	text: string;
	/** Present when Coinbase has no trades before coverage starts (the listing). */
	floorNote: string | null;
};

/** Summarize one ingest state for the progress line. */
export function downloadProgress(status: IngestStatus): DownloadProgress {
	const state = status.state;
	const received = state.watch_covered_candle_count ?? state.received_candle_count ?? null;
	const expected = state.watch_expected_candle_count ?? state.expected_candle_count ?? null;
	const done = state.watch_complete === true;
	const failed = !done && typeof state.failure_code === 'string' && state.failure_code !== '';
	const watchStatus = done ? 'complete' : state.status === 'never_run' ? 'waiting' : 'backfilling';
	const counts =
		received === null
			? 'no candles yet'
			: expected === null
				? `${received} candles`
				: `${received} of ${expected} candles`;
	const text = done
		? `Complete · ${counts}`
		: watchStatus === 'waiting'
			? 'Queued · waiting for the market-data worker'
			: `Backfilling · ${counts}${failed ? ` · last attempt: ${state.failure_code}` : ''}`;
	const floor = state.history_floor_at ?? null;
	return {
		done,
		failed,
		received,
		expected,
		watchStatus,
		text,
		floorNote:
			floor === null
				? null
				: `Coinbase has no trades before ${formatUtc(floor)} (the listing); coverage starts there.`
	};
}

function formatUtc(value: string): string {
	const parsed = new Date(value);
	if (Number.isNaN(parsed.getTime())) return value;
	return `${parsed.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

async function dataRequest<T>(url: string, init?: RequestInit): Promise<T> {
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
		const body = (await response.json().catch(() => ({}))) as {
			detail?: string | { message?: string };
		};
		const detail =
			typeof body.detail === 'string'
				? body.detail
				: (body.detail?.message ?? 'no details returned');
		throw new DataLaneError(
			response.status,
			`The data request failed (HTTP ${response.status}): ${detail}`
		);
	}
	return (await response.json()) as T;
}
