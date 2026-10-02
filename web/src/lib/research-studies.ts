import {
	isAcceptedResearchJob,
	researchJobFailureMessage,
	researchJobFailureStatus,
	waitForResearchJob,
	type ResearchJobAccepted
} from '$lib/research-jobs';
import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';

/** Research-study HTTP helpers. Studies compose existing backtests. */

export type StudyKind =
	'oos_holdout' | 'walk_forward' | 'cross_market' | 'parameter_sweep' | 'walk_forward_optimization';
export type FoldMode = 'rolling' | 'anchored';
export type SelectionMetric =
	'total_return_fraction' | 'total_net_pnl' | 'maximum_drawdown_fraction';
export type SweepAxisTarget =
	'indicator' | 'sizing' | 'exits' | 'execution' | 'entry_literal' | 'htf_literal';
export type SweepParameter =
	| 'period'
	| 'fast_period'
	| 'slow_period'
	| 'signal_period'
	| 'k_period'
	| 'd_period'
	| 'stdev_multiplier'
	| 'value'
	| 'multiplier'
	| 'atr_period'
	| 'step'
	| 'max_step'
	| 'tenkan_period'
	| 'kijun_period'
	| 'senkou_b_period'
	| 'rsi_period'
	| 'stoch_period'
	| 'short_period'
	| 'medium_period'
	| 'long_period'
	| 'annualization_periods'
	| 'offset'
	| 'risk_fraction'
	| 'min_quote_notional'
	| 'max_quote_notional'
	| 'initial_stop_multiple'
	| 'take_profit_multiple'
	| 'trailing_stop_multiple'
	| 'max_bars_held'
	| 'max_entry_wait_bars'
	| 'literal';

const PARAMETERS_BY_TARGET: Record<SweepAxisTarget, SweepParameter[]> = {
	indicator: [
		'period',
		'fast_period',
		'slow_period',
		'signal_period',
		'k_period',
		'd_period',
		'stdev_multiplier',
		'value',
		'multiplier',
		'atr_period',
		'step',
		'max_step',
		'tenkan_period',
		'kijun_period',
		'senkou_b_period',
		'rsi_period',
		'stoch_period',
		'short_period',
		'medium_period',
		'long_period',
		'annualization_periods',
		'offset'
	],
	sizing: ['risk_fraction', 'min_quote_notional', 'max_quote_notional'],
	exits: [
		'initial_stop_multiple',
		'take_profit_multiple',
		'trailing_stop_multiple',
		'max_bars_held'
	],
	execution: ['max_entry_wait_bars'],
	entry_literal: ['literal'],
	htf_literal: ['literal']
};

export type ParameterAxis = {
	target?: SweepAxisTarget;
	indicator_id?: string;
	parameter: SweepParameter;
	values: string[];
	condition_operator?: string;
};

export type ResearchStudyRequest = {
	schema_version: 'thytrader-research-study-v1';
	kind: StudyKind;
	evaluation_start: string;
	evaluation_end: string;
	initial_quote_balance: string;
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	/** Optional constant total bid-ask spread stress (bps); omit for none. */
	spread_bps?: string;
	/** The server snapshots this strategy's current definition. */
	strategy_id?: string;
	dataset_fingerprint?: string;
	htf_dataset_fingerprint?: string;
	indicator_dataset_fingerprints?: { timeframe: string; dataset_fingerprint: string }[];
	oos_fraction?: string;
	embargo_bars?: number;
	in_sample_bars?: number;
	out_of_sample_bars?: number;
	step_bars?: number;
	fold_mode?: FoldMode;
	candidate_strategy_ids?: string[];
	parameter_axes?: ParameterAxis[];
	selection_metric?: SelectionMetric;
};

export type StudyWindowResult = {
	label: string;
	role: 'in_sample' | 'out_of_sample' | 'full_window' | 'sweep_candidate';
	fold_index: number;
	product_id: string;
	run_fingerprint: string;
	result_fingerprint: string;
	strategy_fingerprint?: string;
	selected?: boolean;
	evaluation_start: string;
	evaluation_end: string;
	summary: {
		total_return_fraction: string;
		trade_count: number;
		win_rate: string;
		maximum_drawdown_fraction: string;
	};
};

export type StitchedOosEquity = {
	available: boolean;
	reason?: string | null;
	initial_equity?: string | null;
	final_equity?: string | null;
	total_return_fraction?: string | null;
	maximum_drawdown_fraction?: string | null;
	point_count: number;
};

export type ResearchStudy = {
	study_fingerprint: string;
	kind: StudyKind;
	windows: StudyWindowResult[];
	aggregate: {
		oos_window_count: number;
		oos_trade_count: number;
		mean_oos_return_fraction: string | null;
		mean_is_return_fraction: string | null;
		is_oos_return_gap: string | null;
	};
	warnings: string[];
	selection_metric?: SelectionMetric | null;
	stitched_oos_equity?: StitchedOosEquity | null;
};

/** One candidate's sweep assignment, e.g. `{ "fast.period": 20, "slow.period": 200 }`. */
export type AxisValues = Record<string, number | string>;

/** One child row of a study summary (ADR 0094): its candidate's axis values and bounds. */
export type StudyWindowPnl = {
	label: string;
	role: StudyWindowResult['role'];
	fold_index: number;
	product_id: string;
	evaluation_start: string;
	evaluation_end: string;
	result_fingerprint: string;
	strategy_fingerprint: string;
	axis_values: AxisValues;
	total_net_pnl: string;
	total_return_fraction: string;
	trade_count: number;
	selected?: boolean;
};

/** Every window of one candidate summed: OOS robustness across the grid, not just the winner. */
export type StudyCandidateAggregate = {
	strategy_fingerprint: string;
	product_id: string;
	axis_values: AxisValues;
	window_count: number;
	selected_window_count: number;
	in_sample_window_count: number;
	in_sample_total_net_pnl: string | null;
	oos_window_count: number;
	oos_total_net_pnl: string | null;
	oos_positive_window_count: number;
	oos_trade_count: number;
	full_window_count: number;
	full_window_total_net_pnl: string | null;
};

export type ResearchStudySummary = {
	study_fingerprint: string;
	kind: StudyKind;
	window_count: number;
	window_pnl: StudyWindowPnl[];
	candidates: StudyCandidateAggregate[];
	stitched_oos_points_downsampled?: boolean;
};

/** `fast.period=20 · slow.period=200`, or `—` when the study has a single candidate. */
export function formatAxisValues(values: AxisValues | undefined): string {
	const entries = Object.entries(values ?? {});
	if (entries.length === 0) return '—';
	return entries.map(([label, value]) => `${label}=${value}`).join(' · ');
}

/** `2026-01-01 → 2026-01-04` for one child window (UTC dates). */
export function formatWindowBounds(start: string, end: string): string {
	return `${start.slice(0, 10)} → ${end.slice(0, 10)}`;
}

/** Read one persisted study's summary: axis values, bounds, and per-candidate sums. */
export async function fetchResearchStudySummary(
	studyFingerprint: string
): Promise<ResearchStudySummary> {
	const response = await fetch(`/api/v1/research/studies/${encodeURIComponent(studyFingerprint)}`, {
		headers: { Accept: 'application/json' }
	});
	if (!response.ok) {
		throw new Error(`Could not load the study summary (HTTP ${response.status}).`);
	}
	return (await response.json()) as ResearchStudySummary;
}

export type StrategyTemplate = { id: string; name: string; description: string };

export function parseParameterAxisValues(raw: string): string[] {
	return raw
		.split(',')
		.map((item) => item.trim())
		.filter((item) => item !== '');
}

export function parametersForTarget(target: SweepAxisTarget): SweepParameter[] {
	return PARAMETERS_BY_TARGET[target];
}

export function axisNeedsIndicator(target: SweepAxisTarget): boolean {
	return target === 'indicator' || target === 'entry_literal' || target === 'htf_literal';
}

export async function listStrategyTemplates(): Promise<StrategyTemplate[]> {
	const response = await fetch('/api/v1/research/templates');
	if (!response.ok) {
		throw new Error(`Could not load strategy templates (HTTP ${response.status}).`);
	}
	const body = (await response.json()) as { templates: StrategyTemplate[] };
	return body.templates;
}

export async function submitResearchStudy(request: ResearchStudyRequest): Promise<ResearchStudy> {
	await ensureBrowserCsrfSession();
	const response = await fetch('/api/v1/research/studies', {
		method: 'POST',
		headers: { 'content-type': 'application/json', ...mutationHeaders() },
		body: JSON.stringify(request)
	});
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as {
			detail?: string | { message?: string };
		};
		const detail =
			typeof body.detail === 'string'
				? body.detail
				: (body.detail?.message ?? 'no details returned');
		throw new Error(`The research study failed (HTTP ${response.status}): ${detail}`);
	}
	const body = (await response.json()) as ResearchStudy | ResearchJobAccepted;
	if (!isAcceptedResearchJob(body)) return body;
	return finishQueuedStudy(body);
}

/**
 * A study longer than the API's synchronous wait comes back as HTTP 202 with its job
 * (ADR 0092): poll the job, then read the persisted study it produced.
 */
async function finishQueuedStudy(accepted: ResearchJobAccepted): Promise<ResearchStudy> {
	const job = await waitForResearchJob(accepted.job_id);
	if (job.status !== 'completed' || !job.study_fingerprint) {
		throw new Error(
			`The research study failed (HTTP ${researchJobFailureStatus(job)}): ${researchJobFailureMessage(job)}`
		);
	}
	const study = await fetch(
		`/api/v1/research/studies/${encodeURIComponent(job.study_fingerprint)}?detail=full`,
		{ headers: { Accept: 'application/json' } }
	);
	if (!study.ok) {
		throw new Error(`Could not load the finished research study (HTTP ${study.status}).`);
	}
	return (await study.json()) as ResearchStudy;
}
