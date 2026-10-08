/**
 * Plan and submit logic of the Test stage's research launch panel, kept free
 * of UI state: the launch form and study draft shapes, which verified datasets
 * a launch needs, the evaluation window, and the backtest / study request
 * bodies. `ResearchLaunchPanel.svelte` owns the state and the fetches.
 */
import { optionalSpreadStress } from '$lib/backtests';
import {
	axisNeedsIndicator,
	parseParameterAxisValues,
	type FoldMode,
	type ResearchStudyRequest,
	type SelectionMetric,
	type SweepAxisTarget,
	type SweepParameter
} from '$lib/research-studies';
import {
	datasetEvaluationWindow,
	formatUtcInputValue,
	parseUtcInputValue,
	researchWindowHint,
	strategyErrorCode,
	unboundIndicatorTimeframes,
	type BacktestLaunchInput,
	type BuilderModel,
	type Dataset
} from '$lib/strategies';

/** The run bar and Advanced options, as text inputs edit them. */
export type LaunchForm = {
	dataset_fingerprint: string;
	htf_dataset_fingerprint: string;
	indicator_dataset_fingerprints: Record<string, string>;
	evaluation_start: string;
	evaluation_end: string;
	initial_quote_balance: string;
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	/** Optional spread stress in bps; blank or zero sends none. */
	spread_bps: string;
};

export function defaultLaunchForm(): LaunchForm {
	return {
		dataset_fingerprint: '',
		htf_dataset_fingerprint: '',
		indicator_dataset_fingerprints: {},
		evaluation_start: '',
		evaluation_end: '',
		initial_quote_balance: '10000',
		maker_fee_rate: '',
		taker_fee_rate: '',
		fixed_slippage_bps: '10',
		spread_bps: ''
	};
}

/** Study kinds the panel composes (cross-market studies stay on the research CLI). */
export type LaunchStudyKind =
	'oos_holdout' | 'walk_forward' | 'parameter_sweep' | 'walk_forward_optimization';

/** The "Run a study" settings: fold geometry and one parameter axis. */
export type StudyDraft = {
	kind: LaunchStudyKind;
	oosFraction: string;
	inSampleBars: string;
	outOfSampleBars: string;
	stepBars: string;
	foldMode: FoldMode;
	axisTarget: SweepAxisTarget;
	axisIndicatorId: string;
	axisParameter: SweepParameter;
	axisValues: string;
	axisConditionOperator: string;
	selectionMetric: SelectionMetric;
};

export function defaultStudyDraft(): StudyDraft {
	return {
		kind: 'walk_forward',
		oosFraction: '0.3',
		inSampleBars: '720',
		outOfSampleBars: '168',
		stepBars: '168',
		foldMode: 'rolling',
		axisTarget: 'indicator',
		axisIndicatorId: 'fast',
		axisParameter: 'period',
		axisValues: '12,26',
		axisConditionOperator: '',
		selectionMetric: 'total_return_fraction'
	};
}

export function usesFoldGeometry(kind: LaunchStudyKind): boolean {
	return kind === 'walk_forward' || kind === 'walk_forward_optimization';
}

export function usesParameterAxes(kind: LaunchStudyKind): boolean {
	return kind === 'parameter_sweep' || kind === 'walk_forward_optimization';
}

export function studyGeometryFields(study: StudyDraft): Partial<ResearchStudyRequest> {
	if (study.kind === 'oos_holdout') {
		return { oos_fraction: study.oosFraction, embargo_bars: 0 };
	}
	if (usesFoldGeometry(study.kind)) {
		return {
			in_sample_bars: Number(study.inSampleBars),
			out_of_sample_bars: Number(study.outOfSampleBars),
			step_bars: Number(study.stepBars),
			fold_mode: study.foldMode
		};
	}
	return {};
}

/** The parameter axis and selection metric, or the message that blocks the launch. */
export function studyCandidateFields(study: StudyDraft): Partial<ResearchStudyRequest> | string {
	if (!usesParameterAxes(study.kind)) return {};
	const values = parseParameterAxisValues(study.axisValues);
	if (values.length < 2) {
		return 'Enter at least two comma-separated parameter values.';
	}
	if (axisNeedsIndicator(study.axisTarget) && study.axisIndicatorId.trim() === '') {
		return 'Enter the indicator id this axis locates.';
	}
	const axis: NonNullable<ResearchStudyRequest['parameter_axes']>[number] = {
		parameter: study.axisParameter,
		values
	};
	if (study.axisTarget !== 'indicator') {
		axis.target = study.axisTarget;
	}
	if (axisNeedsIndicator(study.axisTarget)) {
		axis.indicator_id = study.axisIndicatorId.trim();
	}
	if (study.axisConditionOperator.trim() !== '') {
		axis.condition_operator = study.axisConditionOperator.trim();
	}
	return {
		parameter_axes: [axis],
		selection_metric: study.selectionMetric
	};
}

/** One verified dataset as a select option: clock and UTC coverage. */
export function datasetOptionLabel(dataset: Dataset): string {
	return `${dataset.timeframe} · ${formatUtcInputValue(new Date(dataset.starts_at)).replace(
		'T',
		' '
	)} – ${formatUtcInputValue(new Date(dataset.ends_at)).replace('T', ' ')} UTC`;
}

export function datasetsForTimeframe(datasets: Dataset[], timeframe: string): Dataset[] {
	return datasets.filter((dataset) => dataset.timeframe === timeframe);
}

/** HTF datasets for the model's filter clock (none without a filter). */
export function htfLaunchDatasets(datasets: Dataset[], model: BuilderModel): Dataset[] {
	const timeframe = model.htf_filter?.timeframe;
	if (timeframe === undefined) return [];
	return datasets.filter((dataset) => dataset.timeframe === timeframe);
}

/** Indicator clocks that are neither the decision clock nor the HTF clock. */
export function extraLaunchTimeframes(model: BuilderModel): string[] {
	return unboundIndicatorTimeframes(model.indicators, model.timeframe, model.htf_filter?.timeframe);
}

export function indicatorLaunchBindings(
	model: BuilderModel,
	fingerprints: Record<string, string>
): { timeframe: string; dataset_fingerprint: string }[] {
	return unboundIndicatorTimeframes(model.indicators, model.timeframe, model.htf_filter?.timeframe)
		.map((timeframe) => ({
			timeframe,
			dataset_fingerprint: fingerprints[timeframe] ?? ''
		}))
		.filter((binding) => binding.dataset_fingerprint !== '');
}

export function missingExtraLaunchDatasets(
	model: BuilderModel,
	fingerprints: Record<string, string>
): boolean {
	return extraLaunchTimeframes(model).some((timeframe) => (fingerprints[timeframe] ?? '') === '');
}

/** The selected decision dataset's evaluable window after warmup, or null. */
export function launchWindowBounds(
	datasets: Dataset[],
	datasetFingerprint: string,
	model: BuilderModel
): { min: string; max: string } | null {
	const dataset = datasets.find(
		(candidate) => candidate.content_fingerprint === datasetFingerprint
	);
	if (dataset === undefined) return null;
	return datasetEvaluationWindow(dataset, model.warmup_bars ?? 0, model.timeframe ?? '1h');
}

export function launchWindowHint(
	bounds: { min: string; max: string } | null,
	model: BuilderModel
): string | null {
	if (bounds === null) return null;
	return researchWindowHint(bounds, model.warmup_bars ?? 0, model.timeframe);
}

/** "Full coverage", the chosen UTC dates, or "Not set". */
export function periodLabel(form: LaunchForm, bounds: { min: string; max: string } | null): string {
	if (form.evaluation_start === '' || form.evaluation_end === '') return 'Not set';
	if (
		bounds !== null &&
		form.evaluation_start === bounds.min &&
		form.evaluation_end === bounds.max
	) {
		return 'Full coverage';
	}
	return `${form.evaluation_start.slice(0, 10)} → ${form.evaluation_end.slice(0, 10)}`;
}

/**
 * The backtest launch body (also the shared part of a study request). Throws
 * when a window field is not a valid UTC input value.
 */
export function backtestLaunchInput(
	strategyId: string,
	form: LaunchForm,
	model: BuilderModel
): BacktestLaunchInput {
	return {
		strategy_id: strategyId,
		dataset_fingerprint: form.dataset_fingerprint,
		...(form.htf_dataset_fingerprint === ''
			? {}
			: { htf_dataset_fingerprint: form.htf_dataset_fingerprint }),
		...(indicatorLaunchBindings(model, form.indicator_dataset_fingerprints).length === 0
			? {}
			: {
					indicator_dataset_fingerprints: indicatorLaunchBindings(
						model,
						form.indicator_dataset_fingerprints
					)
				}),
		evaluation_start: parseUtcInputValue(form.evaluation_start).toISOString(),
		evaluation_end: parseUtcInputValue(form.evaluation_end).toISOString(),
		initial_quote_balance: form.initial_quote_balance,
		maker_fee_rate: form.maker_fee_rate,
		taker_fee_rate: form.taker_fee_rate,
		fixed_slippage_bps: form.fixed_slippage_bps,
		...optionalSpreadStress(form.spread_bps)
	};
}

/** A composed study request over the same window, datasets, and costs as a backtest. */
export function researchStudyRequest(
	strategyId: string,
	form: LaunchForm,
	model: BuilderModel,
	study: StudyDraft,
	candidateFields: Partial<ResearchStudyRequest>
): ResearchStudyRequest {
	return {
		schema_version: 'thytrader-research-study-v1',
		kind: study.kind,
		...backtestLaunchInput(strategyId, form, model),
		...studyGeometryFields(study),
		...candidateFields
	};
}

/** Operator-facing reason a launch was refused. */
export function launchErrorMessage(caught: unknown): string {
	return strategyErrorCode(caught) === 'strategy_invalid'
		? 'The saved definition is not valid, so nothing was started. Fix it in Build and save first.'
		: caught instanceof Error
			? caught.message
			: 'Backtest submission failed.';
}
