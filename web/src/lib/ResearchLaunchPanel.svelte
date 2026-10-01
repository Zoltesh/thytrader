<script lang="ts">
	import { resolve } from '$app/paths';
	import { formatPercent, optionalSpreadStress } from '$lib/backtests';
	import BacktestModelDisclosure from '$lib/BacktestModelDisclosure.svelte';
	import DataReadinessPanel from '$lib/workspace/DataReadinessPanel.svelte';
	import { productIdQuote } from '$lib/deployment-detail';
	import {
		RESEARCH_FEE_ENGINE_NOTE,
		fetchFeeProfile,
		formatFeeProfileAsOf,
		formatResearchFeeSourceChip,
		readResearchFeeSuggestion,
		researchFeeFieldSource,
		shouldPrefillResearchFeeRates,
		type ResearchFeeSuggestion
	} from '$lib/fees';
	import {
		axisNeedsIndicator,
		parametersForTarget,
		parseParameterAxisValues,
		submitResearchStudy,
		type ResearchStudy,
		type ResearchStudyRequest,
		type SelectionMetric,
		type SweepAxisTarget,
		type SweepParameter
	} from '$lib/research-studies';
	import {
		datasetEvaluationWindow,
		formatUtcInputValue,
		latestDatasets,
		listDatasets,
		parseUtcInputValue,
		researchWindowHint,
		strategyErrorCode,
		submitBacktest,
		unboundIndicatorTimeframes,
		type BacktestLaunchInput,
		type BuilderModel,
		type Dataset
	} from '$lib/strategies';

	let {
		strategyId,
		productId,
		model,
		currentFingerprint,
		onBacktestLaunched
	}: {
		strategyId: string;
		productId: string;
		/** The strategy's current valid definition (the server snapshots it on launch). */
		model: BuilderModel;
		/** Fingerprint the launch snapshot gets ('' blocks launch). */
		currentFingerprint: string;
		/** Called with the new result fingerprint after a single backtest is published. */
		onBacktestLaunched: (resultFingerprint: string) => void;
	} = $props();

	let launchDatasets = $state<Dataset[]>([]);
	let launchDatasetsLoading = $state(false);
	let launchDatasetError = $state<string | null>(null);
	let launchError = $state<string | null>(null);
	let launching = $state(false);
	let studyKind = $state<
		'oos_holdout' | 'walk_forward' | 'parameter_sweep' | 'walk_forward_optimization'
	>('walk_forward');
	let oosFraction = $state('0.3');
	let inSampleBars = $state('720');
	let outOfSampleBars = $state('168');
	let stepBars = $state('168');
	let foldMode = $state<'rolling' | 'anchored'>('rolling');
	let axisTarget = $state<SweepAxisTarget>('indicator');
	let axisIndicatorId = $state('fast');
	let axisParameter = $state<SweepParameter>('period');
	let axisValues = $state('12,26');
	let axisConditionOperator = $state('');
	let selectionMetric = $state<SelectionMetric>('total_return_fraction');
	let studyResult = $state<ResearchStudy | null>(null);
	let selectedStrategyFingerprint = $state('');
	let launchForm = $state({
		dataset_fingerprint: '',
		htf_dataset_fingerprint: '',
		indicator_dataset_fingerprints: {} as Record<string, string>,
		evaluation_start: '',
		evaluation_end: '',
		initial_quote_balance: '10000',
		maker_fee_rate: '',
		taker_fee_rate: '',
		fixed_slippage_bps: '10',
		/** Optional spread stress in bps; blank or zero sends none. */
		spread_bps: ''
	});
	let studyOpen = $state(false);
	let latestFeeSuggestion = $state<ResearchFeeSuggestion | null>(null);
	let appliedFeeSuggestion = $state<ResearchFeeSuggestion | null>(null);
	let feeSuggestionLoading = $state(false);
	let feeFieldsTouched = $state(false);
	let feeSuggestionRequestId = 0;
	let catalogRequestId = 0;

	const feeFieldSource = $derived(
		researchFeeFieldSource({
			makerFeeRate: launchForm.maker_fee_rate,
			takerFeeRate: launchForm.taker_fee_rate,
			applied: appliedFeeSuggestion,
			latest: latestFeeSuggestion,
			loading: feeSuggestionLoading
		})
	);
	const feeSourceChip = $derived(
		formatResearchFeeSourceChip(
			feeFieldSource,
			appliedFeeSuggestion !== null
				? formatFeeProfileAsOf(appliedFeeSuggestion.fetchedAt)
				: latestFeeSuggestion !== null
					? formatFeeProfileAsOf(latestFeeSuggestion.fetchedAt)
					: null
		)
	);

	let loadedKey = $state('');
	$effect(() => {
		const key = `${strategyId}:${currentFingerprint}`;
		if (loadedKey === key) return;
		const strategyChanged = loadedKey.split(':')[0] !== strategyId;
		loadedKey = key;
		selectedStrategyFingerprint = currentFingerprint;
		launchError = null;
		studyResult = null;
		void loadLaunchDatasets();
		if (strategyChanged) {
			feeFieldsTouched = false;
			appliedFeeSuggestion = null;
			latestFeeSuggestion = null;
			void loadFeeSuggestion();
		}
	});

	function periodLabel(): string {
		const bounds = launchWindowBounds();
		if (launchForm.evaluation_start === '' || launchForm.evaluation_end === '') return 'Not set';
		if (
			bounds !== null &&
			launchForm.evaluation_start === bounds.min &&
			launchForm.evaluation_end === bounds.max
		) {
			return 'Full coverage';
		}
		return `${launchForm.evaluation_start.slice(0, 10)} → ${launchForm.evaluation_end.slice(0, 10)}`;
	}

	function applyFeeSuggestion(suggestion: ResearchFeeSuggestion): void {
		launchForm.maker_fee_rate = suggestion.makerFeeRate;
		launchForm.taker_fee_rate = suggestion.takerFeeRate;
		appliedFeeSuggestion = suggestion;
		feeFieldsTouched = false;
	}

	function onFeeFieldInput(): void {
		feeFieldsTouched = true;
	}

	async function loadFeeSuggestion(): Promise<void> {
		const requestId = ++feeSuggestionRequestId;
		feeSuggestionLoading = true;
		try {
			const profile = await fetchFeeProfile();
			if (requestId !== feeSuggestionRequestId) return;
			const suggestion = readResearchFeeSuggestion(profile);
			latestFeeSuggestion = suggestion;
			if (
				shouldPrefillResearchFeeRates({
					makerFeeRate: launchForm.maker_fee_rate,
					takerFeeRate: launchForm.taker_fee_rate,
					touched: feeFieldsTouched,
					suggestion
				}) &&
				suggestion !== null
			) {
				applyFeeSuggestion(suggestion);
			}
		} catch {
			if (requestId !== feeSuggestionRequestId) return;
			latestFeeSuggestion = null;
		} finally {
			if (requestId === feeSuggestionRequestId) {
				feeSuggestionLoading = false;
			}
		}
	}

	function usesFoldGeometry(): boolean {
		return studyKind === 'walk_forward' || studyKind === 'walk_forward_optimization';
	}

	function usesParameterAxes(): boolean {
		return studyKind === 'parameter_sweep' || studyKind === 'walk_forward_optimization';
	}

	function studyGeometryFields(): Partial<ResearchStudyRequest> {
		if (studyKind === 'oos_holdout') {
			return { oos_fraction: oosFraction, embargo_bars: 0 };
		}
		if (usesFoldGeometry()) {
			return {
				in_sample_bars: Number(inSampleBars),
				out_of_sample_bars: Number(outOfSampleBars),
				step_bars: Number(stepBars),
				fold_mode: foldMode
			};
		}
		return {};
	}

	function studyCandidateFields(): Partial<ResearchStudyRequest> | string {
		if (!usesParameterAxes()) return {};
		const values = parseParameterAxisValues(axisValues);
		if (values.length < 2) {
			return 'Enter at least two comma-separated parameter values.';
		}
		if (axisNeedsIndicator(axisTarget) && axisIndicatorId.trim() === '') {
			return 'Enter the indicator id this axis locates.';
		}
		const axis: NonNullable<ResearchStudyRequest['parameter_axes']>[number] = {
			parameter: axisParameter,
			values
		};
		if (axisTarget !== 'indicator') {
			axis.target = axisTarget;
		}
		if (axisNeedsIndicator(axisTarget)) {
			axis.indicator_id = axisIndicatorId.trim();
		}
		if (axisConditionOperator.trim() !== '') {
			axis.condition_operator = axisConditionOperator.trim();
		}
		return {
			parameter_axes: [axis],
			selection_metric: selectionMetric
		};
	}

	function onAxisTargetChange(event: Event): void {
		const value = (event.currentTarget as HTMLSelectElement).value as SweepAxisTarget;
		axisTarget = value;
		const allowed = parametersForTarget(value);
		if (!allowed.includes(axisParameter)) {
			axisParameter = allowed[0] ?? 'period';
		}
		if (!axisNeedsIndicator(value)) {
			axisConditionOperator = '';
		}
	}

	async function runLaunch(mode: 'single' | 'study'): Promise<void> {
		if (selectedStrategyFingerprint === '' || launching) return;
		if (launchForm.maker_fee_rate.trim() === '' || launchForm.taker_fee_rate.trim() === '') {
			launchError = 'Enter modeled maker and taker fee rates before launching.';
			return;
		}
		const candidateFields = mode === 'single' ? {} : studyCandidateFields();
		if (typeof candidateFields === 'string') {
			launchError = candidateFields;
			return;
		}
		launching = true;
		launchError = null;
		studyResult = null;
		try {
			if (mode === 'study') {
				const study = await submitResearchStudy({
					schema_version: 'thytrader-research-study-v1',
					kind: studyKind,
					strategy_id: strategyId,
					dataset_fingerprint: launchForm.dataset_fingerprint,
					...(launchForm.htf_dataset_fingerprint === ''
						? {}
						: { htf_dataset_fingerprint: launchForm.htf_dataset_fingerprint }),
					...(indicatorLaunchBindings().length === 0
						? {}
						: { indicator_dataset_fingerprints: indicatorLaunchBindings() }),
					evaluation_start: parseUtcInputValue(launchForm.evaluation_start).toISOString(),
					evaluation_end: parseUtcInputValue(launchForm.evaluation_end).toISOString(),
					initial_quote_balance: launchForm.initial_quote_balance,
					maker_fee_rate: launchForm.maker_fee_rate,
					taker_fee_rate: launchForm.taker_fee_rate,
					fixed_slippage_bps: launchForm.fixed_slippage_bps,
					...optionalSpreadStress(launchForm.spread_bps),
					...studyGeometryFields(),
					...candidateFields
				});
				studyResult = study;
				return;
			}
			const input: BacktestLaunchInput = {
				strategy_id: strategyId,
				dataset_fingerprint: launchForm.dataset_fingerprint,
				...(launchForm.htf_dataset_fingerprint === ''
					? {}
					: { htf_dataset_fingerprint: launchForm.htf_dataset_fingerprint }),
				...(indicatorLaunchBindings().length === 0
					? {}
					: { indicator_dataset_fingerprints: indicatorLaunchBindings() }),
				evaluation_start: parseUtcInputValue(launchForm.evaluation_start).toISOString(),
				evaluation_end: parseUtcInputValue(launchForm.evaluation_end).toISOString(),
				initial_quote_balance: launchForm.initial_quote_balance,
				maker_fee_rate: launchForm.maker_fee_rate,
				taker_fee_rate: launchForm.taker_fee_rate,
				fixed_slippage_bps: launchForm.fixed_slippage_bps,
				...optionalSpreadStress(launchForm.spread_bps)
			};
			const result = await submitBacktest(input);
			onBacktestLaunched(result.result_fingerprint);
		} catch (caught) {
			launchError =
				strategyErrorCode(caught) === 'strategy_invalid'
					? 'The saved definition is not valid, so nothing was started. Fix it in Build and save first.'
					: caught instanceof Error
						? caught.message
						: 'Backtest submission failed.';
		} finally {
			launching = false;
		}
	}

	async function loadLaunchDatasets(): Promise<void> {
		const requestId = ++catalogRequestId;
		const currentProduct = productId;
		launchDatasetsLoading = true;
		launchDatasetError = null;
		try {
			const datasets = await listDatasets();
			if (requestId !== catalogRequestId) return;
			launchDatasets = latestDatasets(datasets.filter((d) => d.product_id === currentProduct));
			const ltf = model.timeframe;
			const preferred = launchDatasets.find((dataset) => dataset.timeframe === ltf);
			if (preferred !== undefined) {
				selectLaunchDataset(preferred);
			} else if (launchDatasets.length > 0) {
				selectLaunchDataset(launchDatasets[0]);
			}
			const htfTimeframe = model.htf_filter?.timeframe;
			if (htfTimeframe !== undefined) {
				launchForm.htf_dataset_fingerprint =
					launchDatasets.find((dataset) => dataset.timeframe === htfTimeframe)
						?.content_fingerprint ?? '';
			} else {
				launchForm.htf_dataset_fingerprint = '';
			}
			const extra: Record<string, string> = {};
			for (const timeframe of unboundIndicatorTimeframes(
				model.indicators,
				model.timeframe,
				model.htf_filter?.timeframe
			)) {
				extra[timeframe] =
					launchDatasets.find((dataset) => dataset.timeframe === timeframe)?.content_fingerprint ??
					'';
			}
			launchForm.indicator_dataset_fingerprints = extra;
		} catch (caught) {
			if (requestId !== catalogRequestId) return;
			launchDatasetError =
				caught instanceof Error ? caught.message : 'Verified datasets are unavailable.';
		} finally {
			if (requestId === catalogRequestId) {
				launchDatasetsLoading = false;
			}
		}
	}

	function decisionLaunchDatasets(): Dataset[] {
		return launchDatasets.filter((dataset) => dataset.timeframe === model.timeframe);
	}

	function htfLaunchDatasets(): Dataset[] {
		const timeframe = model.htf_filter?.timeframe;
		if (timeframe === undefined) return [];
		return launchDatasets.filter((dataset) => dataset.timeframe === timeframe);
	}

	function indicatorLaunchBindings(): { timeframe: string; dataset_fingerprint: string }[] {
		return unboundIndicatorTimeframes(
			model.indicators,
			model.timeframe,
			model.htf_filter?.timeframe
		)
			.map((timeframe) => ({
				timeframe,
				dataset_fingerprint: launchForm.indicator_dataset_fingerprints[timeframe] ?? ''
			}))
			.filter((binding) => binding.dataset_fingerprint !== '');
	}

	function extraLaunchTimeframes(): string[] {
		return unboundIndicatorTimeframes(
			model.indicators,
			model.timeframe,
			model.htf_filter?.timeframe
		);
	}

	function extraLaunchDatasets(timeframe: string): Dataset[] {
		return launchDatasets.filter((dataset) => dataset.timeframe === timeframe);
	}

	function missingExtraLaunchDatasets(): boolean {
		return extraLaunchTimeframes().some(
			(timeframe) => (launchForm.indicator_dataset_fingerprints[timeframe] ?? '') === ''
		);
	}

	function selectLaunchDataset(dataset: Dataset): void {
		launchForm.dataset_fingerprint = dataset.content_fingerprint;
		applyLaunchWindowDefaults();
	}

	function applyLaunchWindowDefaults(): void {
		const dataset = launchDatasets.find(
			(candidate) => candidate.content_fingerprint === launchForm.dataset_fingerprint
		);
		if (dataset === undefined) return;
		const warmup = model.warmup_bars ?? 0;
		const bounds = datasetEvaluationWindow(dataset, warmup, model.timeframe ?? '1h');
		launchForm.evaluation_start = bounds.min;
		launchForm.evaluation_end = bounds.max;
	}

	function launchWindowBounds(): { min: string; max: string } | null {
		const dataset = launchDatasets.find(
			(candidate) => candidate.content_fingerprint === launchForm.dataset_fingerprint
		);
		if (dataset === undefined) return null;
		return datasetEvaluationWindow(dataset, model.warmup_bars ?? 0, model.timeframe ?? '1h');
	}

	function launchWindowHint(): string | null {
		const bounds = launchWindowBounds();
		if (bounds === null) return null;
		return researchWindowHint(bounds, model.warmup_bars ?? 0, model.timeframe);
	}

	/** The spread stress a launch would send, or null when none. */
	const spreadStress = $derived(optionalSpreadStress(launchForm.spread_bps).spread_bps ?? null);

	const launchBlocked = $derived(
		launching ||
			selectedStrategyFingerprint === '' ||
			launchForm.dataset_fingerprint === '' ||
			(model.htf_filter !== null && launchForm.htf_dataset_fingerprint === '') ||
			missingExtraLaunchDatasets() ||
			launchForm.evaluation_start === '' ||
			launchForm.evaluation_end === '' ||
			launchForm.maker_fee_rate.trim() === '' ||
			launchForm.taker_fee_rate.trim() === ''
	);
</script>

<section class="card run-bar" aria-label="Run a backtest">
	{#if currentFingerprint === ''}
		<p class="view-note">Save a valid definition to run a backtest.</p>
	{:else}
		<div class="bar" data-testid="run-bar">
			<label class="f dataset"
				>Verified {model.timeframe} dataset
				<select
					bind:value={launchForm.dataset_fingerprint}
					onchange={() => applyLaunchWindowDefaults()}
				>
					<option value="">Select a verified {productId} {model.timeframe} dataset</option>
					{#each decisionLaunchDatasets() as dataset (dataset.content_fingerprint)}
						<option value={dataset.content_fingerprint}
							>{dataset.timeframe} · {formatUtcInputValue(new Date(dataset.starts_at)).replace(
								'T',
								' '
							)} – {formatUtcInputValue(new Date(dataset.ends_at)).replace('T', ' ')} UTC</option
						>
					{/each}
				</select></label
			>
			{#if model.htf_filter}
				<label class="f"
					>Verified {model.htf_filter.timeframe} HTF dataset
					<select bind:value={launchForm.htf_dataset_fingerprint}>
						<option value=""
							>Select a verified {productId} {model.htf_filter.timeframe} dataset</option
						>
						{#each htfLaunchDatasets() as dataset (dataset.content_fingerprint)}
							<option value={dataset.content_fingerprint}
								>{dataset.timeframe} · {formatUtcInputValue(new Date(dataset.starts_at)).replace(
									'T',
									' '
								)} – {formatUtcInputValue(new Date(dataset.ends_at)).replace('T', ' ')} UTC</option
							>
						{/each}
					</select></label
				>
			{/if}
			{#each extraLaunchTimeframes() as timeframe (timeframe)}
				<label class="f"
					>Verified {timeframe} indicator dataset
					<select
						value={launchForm.indicator_dataset_fingerprints[timeframe] ?? ''}
						onchange={(event) => {
							launchForm.indicator_dataset_fingerprints = {
								...launchForm.indicator_dataset_fingerprints,
								[timeframe]: (event.currentTarget as HTMLSelectElement).value
							};
						}}
					>
						<option value="">Select a verified {productId} {timeframe} dataset</option>
						{#each extraLaunchDatasets(timeframe) as dataset (dataset.content_fingerprint)}
							<option value={dataset.content_fingerprint}
								>{dataset.timeframe} · {formatUtcInputValue(new Date(dataset.starts_at)).replace(
									'T',
									' '
								)} – {formatUtcInputValue(new Date(dataset.ends_at)).replace('T', ' ')} UTC</option
							>
						{/each}
					</select></label
				>
			{/each}
			<div class="f period">
				<span class="f-label" id="run-period-label">Period</span>
				<span class="static" aria-labelledby="run-period-label" data-testid="run-period"
					>{periodLabel()}</span
				>
			</div>
			<label class="f capital"
				>Initial capital ({productIdQuote(productId) ?? 'quote'})
				<input inputmode="decimal" bind:value={launchForm.initial_quote_balance} /></label
			>
			<div class="f fees" role="group" aria-labelledby="run-fees-label">
				<span class="f-label" id="run-fees-label">Fees (maker / taker)</span>
				<div class="pair">
					<input
						inputmode="decimal"
						aria-label="Maker fee rate"
						bind:value={launchForm.maker_fee_rate}
						oninput={onFeeFieldInput}
					/>
					<input
						inputmode="decimal"
						aria-label="Taker fee rate"
						bind:value={launchForm.taker_fee_rate}
						oninput={onFeeFieldInput}
					/>
				</div>
			</div>
			<div class="actions">
				<button
					class="btn"
					class:on={studyOpen}
					type="button"
					aria-expanded={studyOpen}
					aria-controls="run-study-panel"
					onclick={() => (studyOpen = !studyOpen)}>Run a study {studyOpen ? '▴' : '▾'}</button
				>
				<button
					class="btn primary"
					type="button"
					onclick={() => void runLaunch('single')}
					disabled={launchBlocked}>{launching ? 'Running simulation…' : 'Run backtest'}</button
				>
			</div>
		</div>
		{#if launchDatasetsLoading}
			<p class="field-note">Loading verified datasets…</p>
		{:else if launchDatasetError}
			<p class="field-error" role="alert">{launchDatasetError}</p>
		{/if}
		<DataReadinessPanel
			{strategyId}
			{model}
			datasets={launchDatasetsLoading || launchDatasetError !== null ? null : launchDatasets}
			context="test"
			onRefresh={() => void loadLaunchDatasets()}
		/>
		<div class="fee-source-row">
			<span
				class="fee-source-chip"
				class:custom={feeFieldSource === 'custom'}
				class:stale={feeFieldSource === 'stale-suggestion'}
				data-testid="research-fee-source"
				title={latestFeeSuggestion !== null
					? `Schedule ${latestFeeSuggestion.scheduleVersion}${latestFeeSuggestion.scheduleTierId === '' ? '' : `, band ${latestFeeSuggestion.scheduleTierId}`}`
					: undefined}>{feeSourceChip}</span
			>
			<button
				class="secondary fee-source-action"
				type="button"
				onclick={() => void loadFeeSuggestion()}>Reload fee-tier</button
			>
			{#if latestFeeSuggestion !== null && feeFieldSource === 'stale-suggestion'}
				{@const suggestion = latestFeeSuggestion}
				<button
					class="secondary fee-source-action"
					type="button"
					onclick={() => applyFeeSuggestion(suggestion)}>Refresh suggestion</button
				>
			{:else if latestFeeSuggestion !== null && feeFieldSource === 'custom'}
				{@const suggestion = latestFeeSuggestion}
				<button
					class="secondary fee-source-action"
					type="button"
					onclick={() => applyFeeSuggestion(suggestion)}>Apply suggested rates</button
				>
			{/if}
		</div>
		<details class="advanced" data-testid="run-advanced">
			<summary
				>Advanced options <span class="summary-values"
					>slippage {launchForm.fixed_slippage_bps || '—'} bps{spreadStress !== null
						? ` · spread stress ${spreadStress} bps`
						: ''} · {periodLabel()}</span
				></summary
			>
			<div class="advanced-body">
				<div class="launch-grid">
					<label
						>Fixed slippage (bps)
						<input inputmode="decimal" bind:value={launchForm.fixed_slippage_bps} /></label
					>
					<label
						>Spread stress (bps, total bid-ask, optional)
						<input inputmode="decimal" placeholder="0" bind:value={launchForm.spread_bps} /></label
					>
				</div>
				<div class="launch-grid">
					<label
						>Evaluation start
						<input
							type="datetime-local"
							bind:value={launchForm.evaluation_start}
							min={launchWindowBounds()?.min}
							max={launchWindowBounds()?.max}
						/></label
					>
					<label
						>Evaluation end
						<input
							type="datetime-local"
							bind:value={launchForm.evaluation_end}
							min={launchWindowBounds()?.min}
							max={launchWindowBounds()?.max}
						/></label
					>
				</div>
				{#if launchWindowHint() !== null}
					<p class="view-note">{launchWindowHint()}</p>
				{/if}
			</div>
		</details>
		{#if studyOpen}
			<div class="study-body" id="run-study-panel">
				<div class="launch-grid">
					<label
						>Study
						<select bind:value={studyKind}>
							<option value="oos_holdout">OOS holdout</option>
							<option value="walk_forward">Walk-forward</option>
							<option value="parameter_sweep">Parameter sweep</option>
							<option value="walk_forward_optimization">Walk-forward optimization</option>
						</select></label
					>
				</div>
				{#if studyKind === 'oos_holdout'}
					<div class="launch-grid">
						<label
							>OOS fraction (last share) <input
								inputmode="decimal"
								bind:value={oosFraction}
							/></label
						>
					</div>
					<p class="view-note">
						The same rules snapshot is simulated on in-sample then out-of-sample. OOS is the honest
						claim; this does not retune parameters.
					</p>
				{/if}
				{#if studyKind === 'walk_forward' || studyKind === 'walk_forward_optimization'}
					<div class="launch-grid">
						<label>In-sample bars<input inputmode="numeric" bind:value={inSampleBars} /></label>
						<label>OOS bars<input inputmode="numeric" bind:value={outOfSampleBars} /></label>
						<label>Step bars<input inputmode="numeric" bind:value={stepBars} /></label>
						<label
							>Fold mode
							<select bind:value={foldMode}>
								<option value="rolling">Rolling</option>
								<option value="anchored">Anchored</option>
							</select></label
						>
					</div>
					<p class="view-note">
						{#if studyKind === 'walk_forward'}
							Walk-forward validation uses the same rules snapshot on each fold. Non-overlapping OOS
							windows may include a derived stitched equity curve. Cross-market studies stay on the
							research CLI.
						{:else}
							WFO simulates every candidate on every fold and selects only on in-sample
							{selectionMetric}. The matching OOS window is the claim. Stitched equity compounds
							selected OOS returns without interpolating embargo gaps.
						{/if}
					</p>
				{/if}
				{#if studyKind === 'parameter_sweep' || studyKind === 'walk_forward_optimization'}
					<div class="launch-grid">
						<label
							>Axis target
							<select value={axisTarget} onchange={onAxisTargetChange}>
								<option value="indicator">indicator</option>
								<option value="sizing">sizing</option>
								<option value="exits">exits</option>
								<option value="execution">execution</option>
								<option value="entry_literal">entry_literal</option>
								<option value="htf_literal">htf_literal</option>
							</select></label
						>
						{#if axisNeedsIndicator(axisTarget)}
							<label>Indicator id<input bind:value={axisIndicatorId} /></label>
						{/if}
						<label
							>Parameter
							<select bind:value={axisParameter}>
								{#each parametersForTarget(axisTarget) as parameter (parameter)}
									<option value={parameter}>{parameter}</option>
								{/each}
							</select></label
						>
						<label>Axis values (comma-separated) <input bind:value={axisValues} /></label>
						{#if axisTarget === 'entry_literal' || axisTarget === 'htf_literal'}
							<label
								>Condition operator (optional)
								<input bind:value={axisConditionOperator} /></label
							>
						{/if}
						<label
							>Selection metric
							<select bind:value={selectionMetric}>
								<option value="total_return_fraction">Total return</option>
								<option value="total_net_pnl">Total net PnL</option>
								<option value="maximum_drawdown_fraction">Max drawdown</option>
							</select></label
						>
					</div>
					{#if studyKind === 'parameter_sweep'}
						<p class="view-note">
							Each axis cell is one derived candidate on the same window. The aggregate is not an
							out-of-sample claim. Submit records a snapshot of each derived candidate. Product and
							timeframe are not sweepable.
						</p>
					{/if}
				{/if}
				<button
					class="btn"
					type="button"
					onclick={() => void runLaunch('study')}
					disabled={launchBlocked}>{launching ? 'Running simulation…' : 'Run study'}</button
				>
			</div>
		{/if}
		{#if launchError}<p class="view-problem" role="alert">{launchError}</p>{/if}
		<p class="view-note run-lede">
			Runs against a snapshot of the current saved rules. Results are deterministic and reproducible
			research evidence, not a promise: candles don't show queue position or real fills. This stage
			does not start paper or live trading. {RESEARCH_FEE_ENGINE_NOTE}
		</p>
		<BacktestModelDisclosure maxEntryWaitBars={model.execution?.max_entry_wait_bars ?? null} />
	{/if}
</section>
{#if studyResult}
	<div class="view-block" data-testid="research-study-result">
		<h3>Research study</h3>
		<p class="view-note">
			{studyResult.kind} ·
			{studyResult.aggregate.oos_window_count} OOS window(s) · fingerprint
			{studyResult.study_fingerprint.slice(0, 18)}…
		</p>
		{#if studyResult.aggregate.mean_oos_return_fraction !== null}
			<p>
				Mean OOS return {formatPercent(studyResult.aggregate.mean_oos_return_fraction)}
				{#if studyResult.aggregate.is_oos_return_gap !== null}
					· IS−OOS gap {formatPercent(studyResult.aggregate.is_oos_return_gap)}
				{/if}
			</p>
		{/if}
		{#if studyResult.stitched_oos_equity}
			<p class="view-note">
				{#if studyResult.stitched_oos_equity.available && studyResult.stitched_oos_equity.total_return_fraction}
					Stitched OOS return {formatPercent(studyResult.stitched_oos_equity.total_return_fraction)}
					{#if studyResult.stitched_oos_equity.maximum_drawdown_fraction}
						· max drawdown {formatPercent(
							studyResult.stitched_oos_equity.maximum_drawdown_fraction
						)}
					{/if}
				{:else if studyResult.stitched_oos_equity.reason}
					{studyResult.stitched_oos_equity.reason}
				{/if}
			</p>
		{/if}
		{#each studyResult.warnings as warning (warning)}
			<p class="view-note">{warning}</p>
		{/each}
		<table class="results-table" aria-label="Study windows">
			<thead>
				<tr>
					<th scope="col">Window</th>
					<th scope="col">Role</th>
					<th scope="col">Return</th>
					<th scope="col">Trades</th>
				</tr>
			</thead>
			<tbody>
				{#each studyResult.windows as window (window.result_fingerprint)}
					<tr>
						<td>{window.label}</td>
						<td>{window.role}{window.selected === true ? ' · selected' : ''}</td>
						<td>
							<a
								href={resolve(`/backtests?result=${encodeURIComponent(window.result_fingerprint)}`)}
								>{formatPercent(window.summary.total_return_fraction)}</a
							>
						</td>
						<td>{window.summary.trade_count}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<style>
	.run-bar {
		display: grid;
		gap: 10px;
		margin-bottom: var(--space-4);
		padding: 14px 16px;
	}
	.bar {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 10px;
	}
	.f {
		display: grid;
		gap: 4px;
		min-width: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.f.dataset {
		flex: 1 1 200px;
		max-width: 300px;
	}
	.f.period {
		flex: 0 1 130px;
	}
	.f.capital {
		flex: 0 1 120px;
	}
	.f.fees {
		flex: 0 1 160px;
	}
	.f select,
	.f input,
	.static {
		width: 100%;
		min-width: 0;
		min-height: 34px;
		padding: 6px 9px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
		font-size: var(--fs-sm);
		text-overflow: ellipsis;
	}
	.static {
		display: flex;
		align-items: center;
		white-space: nowrap;
		overflow: hidden;
	}
	.pair {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 6px;
	}
	.actions {
		display: flex;
		gap: 8px;
		margin-left: auto;
	}
	.run-lede {
		margin: 0;
		font-size: var(--fs-sm);
	}
	.advanced summary {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		color: var(--muted);
		font-size: var(--fs-sm);
		cursor: pointer;
	}
	.summary-values {
		color: var(--faint);
	}
	.advanced-body {
		display: grid;
		gap: 10px;
		margin-top: 10px;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.study-body {
		display: grid;
		gap: 10px;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.study-body .btn {
		justify-self: start;
	}
	.view-block {
		display: grid;
		gap: 12px;
		margin-bottom: 28px;
	}
	.view-block h3 {
		margin: 0 0 6px;
		font-size: 12px;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.view-block p {
		margin: 0;
		font-size: 13px;
		color: var(--text);
	}
	.view-note {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.view-problem {
		margin: 0;
		color: var(--neg);
		font-size: 13px;
	}
	.launch-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
		gap: 10px;
	}
	.launch-grid label {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.launch-grid input,
	.launch-grid select {
		width: 100%;
		min-height: 34px;
		padding: 6px 9px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
		font: inherit;
		font-size: var(--fs-sm);
	}
	.field-note,
	.field-error {
		margin: 0;
		font-size: var(--fs-xs);
		line-height: 1.35;
	}
	.field-note {
		color: var(--faint);
	}
	.field-error {
		color: var(--neg);
	}
	.fee-source-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
	}
	.fee-source-chip {
		display: inline-flex;
		align-items: center;
		border: 1px solid var(--line-2);
		border-radius: 999px;
		padding: 3px 10px;
		font-size: 11px;
		color: var(--muted);
		background: var(--surface);
	}
	.fee-source-chip.custom {
		color: var(--text);
		border-color: var(--line-strong);
	}
	.fee-source-chip.stale {
		color: var(--warn);
		border-color: var(--warn-line);
	}
	.fee-source-action {
		font-size: 12px;
		padding: 3px 10px;
	}
	.results-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 12px;
	}
	.results-table th,
	.results-table td {
		text-align: left;
		padding: 5px 8px 5px 0;
		border-bottom: 1px solid var(--line);
	}
	.results-table th {
		color: var(--muted);
		font-weight: 500;
		font-size: 11px;
	}
	.results-table tr:last-child td {
		border-bottom: none;
	}
	.results-table td a {
		color: var(--info);
	}
	.secondary {
		color: var(--text);
		background: var(--surface-2);
		border: 1px solid var(--line-2);
		border-radius: 9px;
		padding: 6px 10px;
		cursor: pointer;
	}
	@media (max-width: 640px) {
		.f.dataset,
		.f.period,
		.f.capital,
		.f.fees {
			flex-basis: 100%;
			max-width: none;
		}
		.actions {
			margin-left: 0;
		}
	}
</style>
