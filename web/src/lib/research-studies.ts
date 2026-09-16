/** Research-study HTTP helpers. Studies compose existing backtests. */

export type StudyKind =
	'oos_holdout' | 'walk_forward' | 'cross_market' | 'parameter_sweep' | 'walk_forward_optimization';
export type FoldMode = 'rolling' | 'anchored';
export type SelectionMetric =
	'total_return_fraction' | 'total_net_pnl' | 'maximum_drawdown_fraction';
export type SweepAxisTarget =
	| 'indicator'
	| 'sizing'
	| 'exits'
	| 'execution'
	| 'entry_literal'
	| 'htf_literal';
export type SweepParameter =
	| 'period'
	| 'fast_period'
	| 'slow_period'
	| 'signal_period'
	| 'k_period'
	| 'd_period'
	| 'stdev_multiplier'
	| 'value'
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
		'value'
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
	engine_contract_version: string;
	spread_bps?: string | null;
	strategy_fingerprint?: string;
	dataset_fingerprint?: string;
	htf_dataset_fingerprint?: string;
	indicator_dataset_fingerprints?: { timeframe: string; dataset_fingerprint: string }[];
	oos_fraction?: string;
	embargo_bars?: number;
	in_sample_bars?: number;
	out_of_sample_bars?: number;
	step_bars?: number;
	fold_mode?: FoldMode;
	candidate_strategy_fingerprints?: string[];
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
	engine_contract_version: string;
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

export type StrategyTemplate = { id: string; name: string; description: string };

export function engineContractLabel(version: string): string {
	if (version.endsWith('-v3')) return 'V3';
	if (version.endsWith('-v2')) return 'V2';
	return 'V1';
}

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
	const response = await fetch('/api/v1/research/studies', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
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
	return (await response.json()) as ResearchStudy;
}
