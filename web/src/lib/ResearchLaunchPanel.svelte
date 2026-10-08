<script lang="ts">
	/**
	 * Test-stage run bar: pick verified datasets and costs, then run one
	 * backtest of the current saved rules or compose a research study over the
	 * same window. This component owns the launch state and fetches; request
	 * planning lives in `$lib/research-launch/launch-plan`, and the advanced
	 * options, study settings, fee source, and study result render in
	 * `$lib/research-launch/`.
	 */
	import { optionalSpreadStress } from '$lib/backtests';
	import BacktestModelDisclosure from '$lib/BacktestModelDisclosure.svelte';
	import DataReadinessPanel from '$lib/workspace/DataReadinessPanel.svelte';
	import AdvancedLaunchOptions from '$lib/research-launch/AdvancedLaunchOptions.svelte';
	import FeeSourceRow from '$lib/research-launch/FeeSourceRow.svelte';
	import StudyOptions from '$lib/research-launch/StudyOptions.svelte';
	import StudyResult from '$lib/research-launch/StudyResult.svelte';
	import {
		backtestLaunchInput,
		datasetOptionLabel,
		datasetsForTimeframe,
		defaultLaunchForm,
		defaultStudyDraft,
		extraLaunchTimeframes,
		htfLaunchDatasets,
		launchErrorMessage,
		launchWindowBounds,
		launchWindowHint,
		missingExtraLaunchDatasets,
		periodLabel,
		researchStudyRequest,
		studyCandidateFields
	} from '$lib/research-launch/launch-plan';
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
		fetchResearchStudySummary,
		submitResearchStudy,
		type ResearchStudy,
		type ResearchStudySummary
	} from '$lib/research-studies';
	import {
		latestDatasets,
		listDatasets,
		submitBacktest,
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
		/** Called with the new result fingerprint after a single backtest completes and is saved. */
		onBacktestLaunched: (resultFingerprint: string) => void;
	} = $props();

	let launchDatasets = $state<Dataset[]>([]);
	let launchDatasetsLoading = $state(false);
	let launchDatasetError = $state<string | null>(null);
	let launchError = $state<string | null>(null);
	let launching = $state(false);
	/** "Run a study" settings: kind, fold geometry, and one parameter axis. */
	let study = $state(defaultStudyDraft());
	let studyResult = $state<ResearchStudy | null>(null);
	/** The persisted summary: axis values per row and per-candidate OOS sums (ADR 0094). */
	let studySummary = $state<ResearchStudySummary | null>(null);

	async function loadStudySummary(studyFingerprint: string): Promise<void> {
		try {
			const summary = await fetchResearchStudySummary(studyFingerprint);
			if (studyResult?.study_fingerprint === studyFingerprint) studySummary = summary;
		} catch {
			studySummary = null;
		}
	}
	let selectedStrategyFingerprint = $state('');
	let launchForm = $state(defaultLaunchForm());
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
		studySummary = null;
		void loadLaunchDatasets();
		if (strategyChanged) {
			feeFieldsTouched = false;
			appliedFeeSuggestion = null;
			latestFeeSuggestion = null;
			void loadFeeSuggestion();
		}
	});

	/** The selected decision dataset's evaluable window after warmup, or null. */
	const launchBounds = $derived(
		launchWindowBounds(launchDatasets, launchForm.dataset_fingerprint, model)
	);
	const launchPeriod = $derived(periodLabel(launchForm, launchBounds));
	const launchHint = $derived(launchWindowHint(launchBounds, model));

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

	async function runLaunch(mode: 'single' | 'study'): Promise<void> {
		if (selectedStrategyFingerprint === '' || launching) return;
		if (launchForm.maker_fee_rate.trim() === '' || launchForm.taker_fee_rate.trim() === '') {
			launchError = 'Enter modeled maker and taker fee rates before launching.';
			return;
		}
		const candidateFields = mode === 'single' ? {} : studyCandidateFields(study);
		if (typeof candidateFields === 'string') {
			launchError = candidateFields;
			return;
		}
		launching = true;
		launchError = null;
		studyResult = null;
		studySummary = null;
		try {
			if (mode === 'study') {
				const submitted = await submitResearchStudy(
					researchStudyRequest(strategyId, launchForm, model, study, candidateFields)
				);
				studyResult = submitted;
				studySummary = null;
				void loadStudySummary(submitted.study_fingerprint);
				return;
			}
			const input: BacktestLaunchInput = backtestLaunchInput(strategyId, launchForm, model);
			const result = await submitBacktest(input);
			onBacktestLaunched(result.result_fingerprint);
		} catch (caught) {
			launchError = launchErrorMessage(caught);
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
			for (const timeframe of extraLaunchTimeframes(model)) {
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

	function selectLaunchDataset(dataset: Dataset): void {
		launchForm.dataset_fingerprint = dataset.content_fingerprint;
		applyLaunchWindowDefaults();
	}

	/** Reset the window to the selected dataset's full evaluable coverage. */
	function applyLaunchWindowDefaults(): void {
		const bounds = launchWindowBounds(launchDatasets, launchForm.dataset_fingerprint, model);
		if (bounds === null) return;
		launchForm.evaluation_start = bounds.min;
		launchForm.evaluation_end = bounds.max;
	}

	/** The spread stress a launch would send, or null when none. */
	const spreadStress = $derived(optionalSpreadStress(launchForm.spread_bps).spread_bps ?? null);

	const launchBlocked = $derived(
		launching ||
			selectedStrategyFingerprint === '' ||
			launchForm.dataset_fingerprint === '' ||
			(model.htf_filter !== null && launchForm.htf_dataset_fingerprint === '') ||
			missingExtraLaunchDatasets(model, launchForm.indicator_dataset_fingerprints) ||
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
					{#each datasetsForTimeframe(launchDatasets, model.timeframe) as dataset (dataset.content_fingerprint)}
						<option value={dataset.content_fingerprint}>{datasetOptionLabel(dataset)}</option>
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
						{#each htfLaunchDatasets(launchDatasets, model) as dataset (dataset.content_fingerprint)}
							<option value={dataset.content_fingerprint}>{datasetOptionLabel(dataset)}</option>
						{/each}
					</select></label
				>
			{/if}
			{#each extraLaunchTimeframes(model) as timeframe (timeframe)}
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
						{#each datasetsForTimeframe(launchDatasets, timeframe) as dataset (dataset.content_fingerprint)}
							<option value={dataset.content_fingerprint}>{datasetOptionLabel(dataset)}</option>
						{/each}
					</select></label
				>
			{/each}
			<div class="f period">
				<span class="f-label" id="run-period-label">Period</span>
				<span class="static" aria-labelledby="run-period-label" data-testid="run-period"
					>{launchPeriod}</span
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
		<FeeSourceRow
			{feeFieldSource}
			{feeSourceChip}
			{latestFeeSuggestion}
			onreload={() => void loadFeeSuggestion()}
			onapply={applyFeeSuggestion}
		/>
		<AdvancedLaunchOptions
			bind:launchForm
			{spreadStress}
			period={launchPeriod}
			bounds={launchBounds}
			hint={launchHint}
		/>
		{#if studyOpen}
			<StudyOptions bind:study {launching} {launchBlocked} onrun={() => void runLaunch('study')} />
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
	<StudyResult {studyResult} {studySummary} />
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
