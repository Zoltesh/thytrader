<script lang="ts">
	import { resolve } from '$app/paths';
	import { formatPercent } from '$lib/backtests';
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
		engineContractLabel,
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
		publishedVersionsFor,
		researchWindowHint,
		submitBacktest,
		unboundIndicatorTimeframes,
		type BacktestLaunchInput,
		type BuilderModel,
		type Dataset,
		type StrategyLibraryEntry
	} from '$lib/strategies';

	type VersionResultEntry = {
		result_fingerprint: string;
		published_at: string;
		engine_contract_version: string;
		total_return_fraction: string;
		trade_count: number;
		win_rate: string;
		maximum_drawdown_fraction: string;
	};

	type VersionResultGroup = {
		version: number;
		fingerprint: string;
		loading: boolean;
		error: string | null;
		entries: VersionResultEntry[];
	};

	let { entry, model }: { entry: StrategyLibraryEntry; model: BuilderModel } = $props();

	let launchDatasets = $state<Dataset[]>([]);
	let launchDatasetsLoading = $state(false);
	let launchDatasetError = $state<string | null>(null);
	let launchError = $state<string | null>(null);
	let launching = $state(false);
	let studyKind = $state<
		'single' | 'oos_holdout' | 'walk_forward' | 'parameter_sweep' | 'walk_forward_optimization'
	>('single');
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
		engine: '' as BacktestLaunchInput['engine_contract_version'] | '',
		spread_bps: '8'
	});
	let versionResults = $state<VersionResultGroup[]>([]);
	let latestFeeSuggestion = $state<ResearchFeeSuggestion | null>(null);
	let appliedFeeSuggestion = $state<ResearchFeeSuggestion | null>(null);
	let feeSuggestionLoading = $state(false);
	let feeFieldsTouched = $state(false);
	let feeSuggestionRequestId = 0;
	let catalogRequestId = 0;
	let resultsRequestId = 0;

	const publishedVersions = $derived(publishedVersionsFor(entry));
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

	let loadedStrategyId = $state('');
	$effect(() => {
		const strategyId = entry.strategy_id;
		if (loadedStrategyId === strategyId) return;
		loadedStrategyId = strategyId;
		const next = publishedVersionsFor(entry);
		selectedStrategyFingerprint = next[next.length - 1]?.strategy_fingerprint ?? '';
		launchError = null;
		studyResult = null;
		feeFieldsTouched = false;
		appliedFeeSuggestion = null;
		latestFeeSuggestion = null;
		void loadLaunchDatasets();
		void loadFeeSuggestion();
		void loadVersionResults();
	});

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

	async function runLaunch(): Promise<void> {
		if (selectedStrategyFingerprint === '' || launching) return;
		if (launchForm.engine === '') {
			launchError = 'Select an engine contract before launching.';
			return;
		}
		if (launchForm.maker_fee_rate.trim() === '' || launchForm.taker_fee_rate.trim() === '') {
			launchError = 'Enter modeled maker and taker fee rates before launching.';
			return;
		}
		const candidateFields = studyKind === 'single' ? {} : studyCandidateFields();
		if (typeof candidateFields === 'string') {
			launchError = candidateFields;
			return;
		}
		launching = true;
		launchError = null;
		studyResult = null;
		try {
			if (studyKind !== 'single') {
				const study = await submitResearchStudy({
					schema_version: 'thytrader-research-study-v1',
					kind: studyKind,
					strategy_fingerprint: selectedStrategyFingerprint,
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
					engine_contract_version: launchForm.engine,
					spread_bps:
						launchForm.engine === 'thytrader-bar-backtest-v2' ? launchForm.spread_bps : null,
					...studyGeometryFields(),
					...candidateFields
				});
				studyResult = study;
				return;
			}
			const input: BacktestLaunchInput = {
				strategy_fingerprint: selectedStrategyFingerprint,
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
				engine_contract_version: launchForm.engine,
				spread_bps: launchForm.engine === 'thytrader-bar-backtest-v2' ? launchForm.spread_bps : null
			};
			const result = await submitBacktest(input);
			window.location.assign(
				resolve(`/backtests?result=${encodeURIComponent(result.result_fingerprint)}`)
			);
		} catch (caught) {
			launchError = caught instanceof Error ? caught.message : 'Backtest submission failed.';
		} finally {
			launching = false;
		}
	}

	async function loadLaunchDatasets(): Promise<void> {
		const requestId = ++catalogRequestId;
		const current = entry;
		launchDatasetsLoading = true;
		launchDatasetError = null;
		try {
			const datasets = await listDatasets();
			if (requestId !== catalogRequestId) return;
			launchDatasets = latestDatasets(datasets.filter((d) => d.product_id === current.product_id));
			const ltf = model.timeframe ?? current.timeframe;
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
		return launchDatasets.filter(
			(dataset) => dataset.timeframe === (model.timeframe ?? entry.timeframe)
		);
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
		return researchWindowHint(bounds, model.warmup_bars ?? 0, model.timeframe ?? entry.timeframe);
	}

	async function loadVersionResults(): Promise<void> {
		const requestId = ++resultsRequestId;
		const current = entry;
		const published = publishedVersionsFor(current);
		versionResults = published.map((publishedVersion) => ({
			version: publishedVersion.version,
			fingerprint: publishedVersion.strategy_fingerprint,
			loading: true,
			error: null,
			entries: []
		}));
		await Promise.all(
			published.map(async (publishedVersion, index) => {
				const fingerprint = publishedVersion.strategy_fingerprint;
				try {
					const collected: VersionResultEntry[] = [];
					const pageSize = 20;
					let offset = 0;
					while (true) {
						const response = await fetch(
							`/api/v1/backtests?strategy_fingerprint=${encodeURIComponent(fingerprint)}&limit=${pageSize}&offset=${offset}`
						);
						if (!response.ok) throw new Error(`HTTP ${response.status}`);
						const body = (await response.json()) as {
							entries: {
								result_fingerprint: string;
								published_at: string;
								engine_contract_version: string;
								summary: {
									total_return_fraction: string;
									trade_count: number;
									win_rate: string;
									maximum_drawdown_fraction: string;
								};
							}[];
							returned: number;
						};
						collected.push(
							...body.entries.map((row) => ({
								result_fingerprint: row.result_fingerprint,
								published_at: row.published_at,
								engine_contract_version: row.engine_contract_version,
								total_return_fraction: row.summary.total_return_fraction,
								trade_count: row.summary.trade_count,
								win_rate: row.summary.win_rate,
								maximum_drawdown_fraction: row.summary.maximum_drawdown_fraction
							}))
						);
						if (body.returned < pageSize) break;
						offset += body.returned;
					}
					if (requestId !== resultsRequestId) return;
					versionResults[index] = {
						version: publishedVersion.version,
						fingerprint,
						loading: false,
						error: null,
						entries: collected
					};
				} catch (caught) {
					if (requestId !== resultsRequestId) return;
					versionResults[index] = {
						version: publishedVersion.version,
						fingerprint,
						loading: false,
						error: caught instanceof Error ? caught.message : 'Could not load backtest results.',
						entries: []
					};
				}
			})
		);
	}

	function latestComparisonRows(): (VersionResultEntry & {
		version: number;
		fingerprint: string;
	})[] {
		return versionResults.flatMap((group) => {
			const latest = group.entries[0];
			return latest === undefined
				? []
				: [{ ...latest, version: group.version, fingerprint: group.fingerprint }];
		});
	}
</script>

<div class="view-block">
	<h3>Launch backtest</h3>
	<p class="view-note">
		Runs against the selected immutable version of this strategy. Results are deterministic and
		reproducible. This page does not start paper or live trading.
	</p>
	{#if publishedVersions.length === 0}
		<p class="view-note">Publish this draft before launching a reproducible backtest.</p>
	{:else}
		<div class="launch-grid">
			<label
				>Strategy version
				<select bind:value={selectedStrategyFingerprint}>
					{#each publishedVersions as version (version.strategy_fingerprint)}
						<option value={version.strategy_fingerprint}>Version {version.version}</option>
					{/each}
				</select></label
			>
			<label
				>Verified {entry.timeframe} dataset
				<select
					bind:value={launchForm.dataset_fingerprint}
					onchange={() => applyLaunchWindowDefaults()}
				>
					<option value="">Select a verified {entry.product_id} {entry.timeframe} dataset</option>
					{#each decisionLaunchDatasets() as dataset (dataset.content_fingerprint)}
						<option value={dataset.content_fingerprint}
							>{dataset.timeframe} · {formatUtcInputValue(new Date(dataset.starts_at)).replace(
								'T',
								' '
							)} – {formatUtcInputValue(new Date(dataset.ends_at)).replace('T', ' ')} UTC</option
						>
					{/each}
				</select>
				{#if launchDatasetsLoading}
					<small class="field-note">Loading verified datasets…</small>
				{:else if launchDatasetError}
					<small class="field-error" role="alert">{launchDatasetError}</small>
				{:else if decisionLaunchDatasets().length === 0}
					<small class="field-note">No verified {entry.timeframe} datasets match this market.</small
					>
				{/if}</label
			>
			{#if model.htf_filter}
				<label
					>Verified {model.htf_filter.timeframe} HTF dataset
					<select bind:value={launchForm.htf_dataset_fingerprint}>
						<option value="">Select a verified {model.htf_filter.timeframe} dataset</option>
						{#each htfLaunchDatasets() as dataset (dataset.content_fingerprint)}
							<option value={dataset.content_fingerprint}
								>{dataset.timeframe} · {formatUtcInputValue(new Date(dataset.starts_at)).replace(
									'T',
									' '
								)} – {formatUtcInputValue(new Date(dataset.ends_at)).replace('T', ' ')} UTC</option
							>
						{/each}
					</select>
					{#if htfLaunchDatasets().length === 0}
						<small class="field-note"
							>No verified {model.htf_filter.timeframe} HTF dataset for this market.</small
						>
					{/if}</label
				>
			{/if}
			{#each extraLaunchTimeframes() as timeframe (timeframe)}
				<label
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
						<option value="">Select a verified {timeframe} dataset</option>
						{#each extraLaunchDatasets(timeframe) as dataset (dataset.content_fingerprint)}
							<option value={dataset.content_fingerprint}
								>{dataset.timeframe} · {formatUtcInputValue(new Date(dataset.starts_at)).replace(
									'T',
									' '
								)} – {formatUtcInputValue(new Date(dataset.ends_at)).replace('T', ' ')} UTC</option
							>
						{/each}
					</select>
					{#if extraLaunchDatasets(timeframe).length === 0}
						<small class="field-note">No verified {timeframe} dataset for this market.</small>
					{/if}</label
				>
			{/each}
		</div>
		<div class="launch-grid">
			<label
				>Engine
				<select bind:value={launchForm.engine}>
					<option value="" disabled selected hidden>Select an engine</option>
					<option value="thytrader-bar-backtest-v1">V1 — mark price, fixed slippage</option>
					<option value="thytrader-bar-backtest-v2">V2 — constant spread (bid/ask)</option>
					<option value="thytrader-bar-backtest-v3">V3 — resting maker limit</option>
				</select></label
			>
			<label
				>Study
				<select bind:value={studyKind}>
					<option value="single">Single window</option>
					<option value="oos_holdout">OOS holdout</option>
					<option value="walk_forward">Walk-forward</option>
					<option value="parameter_sweep">Parameter sweep</option>
					<option value="walk_forward_optimization">Walk-forward optimization</option>
				</select></label
			>
			{#if launchForm.engine === 'thytrader-bar-backtest-v2'}
				<label
					>Constant spread (bps, total bid-ask)
					<input inputmode="decimal" bind:value={launchForm.spread_bps} /></label
				>
			{/if}
		</div>
		{#if studyKind === 'oos_holdout'}
			<div class="launch-grid">
				<label
					>OOS fraction (last share) <input inputmode="decimal" bind:value={oosFraction} /></label
				>
			</div>
			<p class="view-note">
				The same published fingerprint is simulated on in-sample then out-of-sample. OOS is the
				honest claim; this does not retune parameters.
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
					Walk-forward validation uses the same published fingerprint on each fold. Non-overlapping
					OOS windows may include a derived stitched equity curve. Cross-market studies stay on the
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
					Each axis cell is one published-shaped candidate on the same window. The aggregate is not
					an out-of-sample claim. Submit publishes missing derived fingerprints. Product and
					timeframe are not sweepable.
				</p>
			{/if}
		{/if}
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
		<div class="launch-grid">
			<label
				>Initial capital (USD)
				<input inputmode="decimal" bind:value={launchForm.initial_quote_balance} /></label
			>
			<label
				>Fixed slippage (bps)
				<input inputmode="decimal" bind:value={launchForm.fixed_slippage_bps} /></label
			>
		</div>
		<div class="launch-grid">
			<label
				>Maker fee rate
				<input
					inputmode="decimal"
					bind:value={launchForm.maker_fee_rate}
					oninput={onFeeFieldInput}
				/></label
			>
			<label
				>Taker fee rate
				<input
					inputmode="decimal"
					bind:value={launchForm.taker_fee_rate}
					oninput={onFeeFieldInput}
				/></label
			>
		</div>
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
		<p class="view-note">{RESEARCH_FEE_ENGINE_NOTE}</p>
		{#if launchError}<p class="view-problem" role="alert">{launchError}</p>{/if}
		<button
			class="refresh launch-button"
			type="button"
			onclick={runLaunch}
			disabled={launching ||
				selectedStrategyFingerprint === '' ||
				launchForm.dataset_fingerprint === '' ||
				(model.htf_filter !== null && launchForm.htf_dataset_fingerprint === '') ||
				missingExtraLaunchDatasets() ||
				launchForm.evaluation_start === '' ||
				launchForm.evaluation_end === '' ||
				launchForm.maker_fee_rate.trim() === '' ||
				launchForm.taker_fee_rate.trim() === ''}
		>
			{launching ? 'Running simulation…' : studyKind === 'single' ? 'Run backtest' : 'Run study'}
		</button>
	{/if}
</div>
{#if studyResult}
	<div class="view-block" data-testid="research-study-result">
		<h3>Research study</h3>
		<p class="view-note">
			{studyResult.kind} · {engineContractLabel(studyResult.engine_contract_version)} ·
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
<div class="view-block">
	<h3>Results by version</h3>
	{#if latestComparisonRows().length > 1}
		{@const comparisonRows = latestComparisonRows()}
		<table class="results-table comparison-table" aria-label="Latest result comparison">
			<thead>
				<tr>
					<th scope="col">Version</th>
					<th scope="col">Engine</th>
					<th scope="col">Return</th>
					<th scope="col">Trades</th>
					<th scope="col">Win rate</th>
					<th scope="col">Max drawdown</th>
				</tr>
			</thead>
			<tbody>
				{#each comparisonRows as row (row.fingerprint)}
					<tr>
						<td>V{row.version}</td>
						<td>{engineContractLabel(row.engine_contract_version)}</td>
						<td>
							<a href={resolve(`/backtests?result=${encodeURIComponent(row.result_fingerprint)}`)}
								>{formatPercent(row.total_return_fraction)}</a
							>
						</td>
						<td>{row.trade_count}</td>
						<td>{formatPercent(row.win_rate)}</td>
						<td>{formatPercent(row.maximum_drawdown_fraction)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
	{#if versionResults.length === 0}
		<p class="view-note">This strategy has no immutable published versions to compare yet.</p>
	{:else}
		{#each versionResults as version (version.fingerprint)}
			<div class="version-block">
				<h4>
					Version {version.version}
					<code>{version.fingerprint.slice(0, 18)}…</code>
				</h4>
				{#if version.loading}
					<p class="view-note">Loading results…</p>
				{:else if version.error}
					<p class="view-problem">{version.error}</p>
				{:else if version.entries.length === 0}
					<p class="view-note">No backtests yet for this version.</p>
				{:else}
					<table class="results-table">
						<thead>
							<tr>
								<th scope="col">Return</th>
								<th scope="col">Engine</th>
								<th scope="col">Trades</th>
								<th scope="col">Win rate</th>
								<th scope="col">Max drawdown</th>
								<th scope="col">Published</th>
							</tr>
						</thead>
						<tbody>
							{#each version.entries as row (row.result_fingerprint)}
								<tr>
									<td>
										<a
											href={resolve(
												`/backtests?result=${encodeURIComponent(row.result_fingerprint)}`
											)}>{formatPercent(row.total_return_fraction)}</a
										>
									</td>
									<td>{engineContractLabel(row.engine_contract_version)}</td>
									<td>{row.trade_count}</td>
									<td>{formatPercent(row.win_rate)}</td>
									<td>{formatPercent(row.maximum_drawdown_fraction)}</td>
									<td>{formatUtcInputValue(new Date(row.published_at)).replace('T', ' ')}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
			</div>
		{/each}
	{/if}
</div>

<style>
	.view-block {
		display: grid;
		gap: 12px;
		margin-bottom: 28px;
	}
	.view-block h3 {
		margin: 0 0 6px;
		font-size: 12px;
		color: #aeb9bb;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.view-block p {
		margin: 0;
		font-size: 13px;
		color: #d8e1e2;
	}
	.view-note {
		color: #aeb9bb;
		font-size: 13px;
	}
	.view-problem {
		color: #f0a3a3;
		font-size: 13px;
	}
	.launch-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 10px;
	}
	.launch-grid label {
		display: grid;
		gap: 4px;
		font-size: 11px;
	}
	.launch-grid input,
	.launch-grid select {
		border: 1px solid #303a3c;
		border-radius: 7px;
		background: #101617;
		color: #edf3f3;
		padding: 7px 9px;
		font: inherit;
		font-size: 12px;
		width: 100%;
	}
	.field-note,
	.field-error {
		font-size: 10px;
		line-height: 1.35;
	}
	.field-note {
		color: #77888b;
	}
	.field-error {
		color: #f0a3a3;
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
		border: 1px solid #303a3c;
		border-radius: 999px;
		padding: 4px 10px;
		font-size: 11px;
		color: #aeb9bb;
		background: #101617;
	}
	.fee-source-chip.custom {
		color: #d8e1e2;
		border-color: #3d4a4c;
	}
	.fee-source-chip.stale {
		color: #e0c48a;
		border-color: #5c4e2f;
	}
	.fee-source-action {
		font-size: 12px;
		padding: 4px 10px;
	}
	.launch-button {
		justify-self: start;
	}
	.version-block {
		border: 1px solid #232d2e;
		border-radius: 8px;
		padding: 10px 12px;
		margin-bottom: 10px;
	}
	.version-block h4 {
		margin: 0 0 8px;
		font-size: 11px;
		color: #aeb9bb;
		text-transform: uppercase;
		letter-spacing: 0.05em;
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
		border-bottom: 1px solid #232d2e;
	}
	.results-table th {
		color: #aeb9bb;
		font-weight: 500;
		font-size: 11px;
	}
	.results-table tr:last-child td {
		border-bottom: none;
	}
	.results-table td a {
		color: #7fd0f0;
	}
	.comparison-table {
		margin: 10px 0 16px;
	}
	.secondary {
		color: #dce4e5;
		background: #151b1d;
		border: 1px solid #303a3c;
		border-radius: 9px;
		padding: 6px 10px;
		cursor: pointer;
	}
	@media (max-width: 640px) {
		.launch-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
