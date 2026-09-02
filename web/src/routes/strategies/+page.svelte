<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { formatPercent } from '$lib/backtests';
	import EngineSupportMatrix from '$lib/EngineSupportMatrix.svelte';
	import {
		archivePublishedStrategy,
		createDraft,
		clonePublishedStrategy,
		datasetEvaluationWindow,
		fetchDraftVersion,
		fetchStrategyHistory,
		fetchStrategySource,
		importStrategy,
		latestDatasets,
		listDatasets,
		listStrategies,
		reviseStrategy,
		submitBacktest,
		toBuilderModel,
		type BacktestLaunchInput,
		type BuilderModel,
		type Dataset,
		type StrategyLibraryEntry,
		type StrategyPublishedVersion,
		type StrategyVersionHistory
	} from '$lib/strategies';
	import { semanticDiff, type SemanticDiff } from '$lib/strategy-diff';
	import { plainEnglishSummary, validateDefinition } from '$lib/strategy-insight';

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

	let entries = $state<StrategyLibraryEntry[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let pendingAction = $state<string | null>(null);
	let showImport = $state(false);
	let importText = $state('');
	let importHint = $state<string | null>(null);
	let viewEntry = $state<StrategyLibraryEntry | null>(null);
	let viewModel = $state<BuilderModel | null>(null);
	let viewLoading = $state(false);
	let viewError = $state<string | null>(null);
	let researchTab = $state<'insight' | 'research' | 'versions'>('insight');
	let launchDatasets = $state<Dataset[]>([]);
	let launchDatasetsLoading = $state(false);
	let launchDatasetError = $state<string | null>(null);
	let launchError = $state<string | null>(null);
	let launching = $state(false);
	let selectedStrategyFingerprint = $state('');
	let launchForm = $state({
		dataset_fingerprint: '',
		evaluation_start: '',
		evaluation_end: '',
		initial_quote_balance: '10000',
		maker_fee_rate: '0.001',
		taker_fee_rate: '0.002',
		fixed_slippage_bps: '10',
		engine: 'thytrader-bar-backtest-v1' as BacktestLaunchInput['engine_contract_version'],
		spread_bps: '8'
	});
	let versionResults = $state<VersionResultGroup[]>([]);
	let versionHistory = $state<StrategyVersionHistory | null>(null);
	let historyLoading = $state(false);
	let historyError = $state<string | null>(null);
	let revising = $state<string | null>(null);
	let reviseError = $state<string | null>(null);
	let diffSelection = $state<{ from: number; to: number }>({ from: 0, to: 0 });
	let diffCache = $state<Record<string, BuilderModel>>({});
	let hoveredId = $state<string | null>(null);
	let barPosition = $state<{ x: number; y: number } | null>(null);
	let barWidth = $state(0);
	let barHeight = $state(0);
	let hideTimer: ReturnType<typeof setTimeout> | null = null;
	let viewRequestId = 0;

	function showBar(event: MouseEvent, entry: StrategyLibraryEntry): void {
		cancelHide();
		hoveredId = entry.strategy_id;
		positionBarForRow((event.currentTarget as HTMLElement).getBoundingClientRect());
	}

	// One stable spot per row: vertically centered, right-aligned. The bar never
	// follows the mouse, so it stays a fixed target while the row is hovered.
	function positionBarForRow(rowRect: DOMRect): void {
		const margin = 10;
		const height = barHeight || 46;
		const width = barWidth || 240;
		// clientWidth excludes the scrollbar, unlike innerWidth.
		const visibleWidth = document.documentElement.clientWidth;
		const x = visibleWidth - margin - width;
		const y = Math.min(
			Math.max(rowRect.top + rowRect.height / 2 - height / 2, margin),
			document.documentElement.clientHeight - height - margin
		);
		barPosition = { x, y };
	}

	function scheduleHide(): void {
		cancelHide();
		hideTimer = setTimeout(() => {
			hoveredId = null;
			barPosition = null;
		}, 140);
	}

	function cancelHide(): void {
		if (hideTimer !== null) {
			clearTimeout(hideTimer);
			hideTimer = null;
		}
	}

	$effect(() => {
		if (hoveredId === null || barWidth === 0 || barHeight === 0) return;
		const row = document.querySelector(`tbody tr[data-strategy-id="${hoveredId}"]`);
		if (row instanceof HTMLElement) positionBarForRow(row.getBoundingClientRect());
	});

	function publishedVersionsFor(entry: StrategyLibraryEntry): StrategyPublishedVersion[] {
		if (entry.status === 'draft') return [];
		if (entry.published_versions.length > 0) return entry.published_versions;
		if (entry.latest_fingerprint && entry.latest_version) {
			return [
				{
					version: entry.latest_version,
					strategy_fingerprint: entry.latest_fingerprint
				}
			];
		}
		return [];
	}

	async function loadVersionResults(entry: StrategyLibraryEntry, requestId: number): Promise<void> {
		if (requestId !== viewRequestId) return;
		const publishedVersions = publishedVersionsFor(entry);
		versionResults = publishedVersions.map((publishedVersion) => ({
			version: publishedVersion.version,
			fingerprint: publishedVersion.strategy_fingerprint,
			loading: true,
			error: null,
			entries: []
		}));
		await Promise.all(
			publishedVersions.map(async (publishedVersion, index) => {
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
					if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
					versionResults[index] = {
						version: publishedVersion.version,
						fingerprint,
						loading: false,
						error: null,
						entries: collected
					};
				} catch (caught) {
					if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
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

	async function runLaunch(): Promise<void> {
		if (!viewEntry || selectedStrategyFingerprint === '' || launching) return;
		launching = true;
		launchError = null;
		try {
			const input: BacktestLaunchInput = {
				strategy_fingerprint: selectedStrategyFingerprint,
				dataset_fingerprint: launchForm.dataset_fingerprint,
				evaluation_start: new Date(launchForm.evaluation_start).toISOString(),
				evaluation_end: new Date(launchForm.evaluation_end).toISOString(),
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

	async function loadLaunchDatasets(entry: StrategyLibraryEntry, requestId: number): Promise<void> {
		launchDatasets = [];
		launchDatasetsLoading = true;
		launchDatasetError = null;
		try {
			const datasets = await listDatasets();
			if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
			// Revisions are cumulative; only the latest one is a useful launch target.
			launchDatasets = latestDatasets(datasets.filter((d) => d.product_id === entry.product_id));
			if (launchDatasets.length > 0) {
				selectLaunchDataset(launchDatasets[0]);
			}
		} catch (caught) {
			if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
			launchDatasetError =
				caught instanceof Error ? caught.message : 'Verified datasets are unavailable.';
		} finally {
			if (requestId === viewRequestId && viewEntry?.strategy_id === entry.strategy_id) {
				launchDatasetsLoading = false;
			}
		}
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
		const warmup = viewModel?.warmup_bars ?? 0;
		const bounds = datasetEvaluationWindow(dataset, warmup);
		launchForm.evaluation_start = bounds.min;
		launchForm.evaluation_end = bounds.max;
	}

	function launchWindowBounds(): { min: string; max: string } | null {
		const dataset = launchDatasets.find(
			(candidate) => candidate.content_fingerprint === launchForm.dataset_fingerprint
		);
		if (dataset === undefined) return null;
		return datasetEvaluationWindow(dataset, viewModel?.warmup_bars ?? 0);
	}

	function launchWindowHint(): string | null {
		const bounds = launchWindowBounds();
		if (bounds === null) return null;
		return `Usable window for this dataset: ${bounds.min.replace('T', ' ')} → ${bounds.max.replace('T', ' ')} (UTC hours). It must fit inside the dataset with ${viewModel?.warmup_bars ?? 0} warmup bars before it and one candle after it.`;
	}

	async function loadVersionHistory(entry: StrategyLibraryEntry, requestId: number): Promise<void> {
		historyLoading = true;
		historyError = null;
		reviseError = null;
		versionHistory = null;
		diffCache = {};
		try {
			const history = await fetchStrategyHistory(entry.strategy_id);
			if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
			versionHistory = history;
			const versions = history.versions;
			if (versions.length >= 2) {
				diffSelection = {
					from: versions[versions.length - 2].version,
					to: versions[versions.length - 1].version
				};
			} else if (versions.length === 1) {
				diffSelection = { from: versions[0].version, to: versions[0].version };
			} else {
				diffSelection = { from: 0, to: 0 };
			}
		} catch (caught) {
			if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
			historyError =
				caught instanceof Error ? caught.message : 'Could not load the version history.';
		} finally {
			if (requestId === viewRequestId && viewEntry?.strategy_id === entry.strategy_id) {
				historyLoading = false;
			}
		}
	}

	async function loadDiffModel(
		entry: StrategyLibraryEntry,
		fingerprint: string
	): Promise<BuilderModel> {
		const cached = diffCache[fingerprint];
		if (cached !== undefined) return cached;
		const source = await fetchStrategySource(fingerprint);
		const model = toBuilderModel(source, 0);
		diffCache = { ...diffCache, [fingerprint]: model };
		return model;
	}

	async function currentDiff(): Promise<SemanticDiff | null> {
		if (!viewEntry || !versionHistory) return null;
		const fromVersion = versionHistory.versions.find(
			(version) => version.version === diffSelection.from
		);
		const toVersion = versionHistory.versions.find(
			(version) => version.version === diffSelection.to
		);
		if (fromVersion === undefined || toVersion === undefined) return null;
		if (diffSelection.from === diffSelection.to) return null;
		try {
			const [before, after] = await Promise.all([
				loadDiffModel(viewEntry, fromVersion.strategy_fingerprint),
				loadDiffModel(viewEntry, toVersion.strategy_fingerprint)
			]);
			return semanticDiff(before, after);
		} catch {
			return null;
		}
	}

	async function reviseFromVersion(
		entry: StrategyLibraryEntry,
		fingerprint: string
	): Promise<void> {
		if (revising !== null) return;
		revising = fingerprint;
		reviseError = null;
		try {
			await reviseStrategy(entry.strategy_id, fingerprint);
			await loadLibrary();
			await loadVersionHistory(entry, viewRequestId);
		} catch (caught) {
			reviseError = caught instanceof Error ? caught.message : 'Could not create a new draft.';
		} finally {
			revising = null;
		}
	}

	function versionHistoryDownloadName(entry: StrategyLibraryEntry, version: number): string {
		return `${entry.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}-v${version}.json`;
	}

	async function exportVersion(
		entry: StrategyLibraryEntry,
		fingerprint: string,
		version: number
	): Promise<void> {
		const source = await fetchStrategySource(fingerprint);
		const blob = new Blob([JSON.stringify(source, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const anchor = document.createElement('a');
		anchor.href = url;
		anchor.download = versionHistoryDownloadName(entry, version);
		anchor.click();
		URL.revokeObjectURL(url);
	}

	async function openView(entry: StrategyLibraryEntry): Promise<void> {
		const requestId = ++viewRequestId;
		viewEntry = entry;
		viewModel = null;
		viewError = null;
		viewLoading = true;
		researchTab = 'insight';
		versionResults = [];
		launchError = null;
		selectedStrategyFingerprint = entry.latest_fingerprint ?? '';
		launchForm.dataset_fingerprint = '';
		void loadLaunchDatasets(entry, requestId);
		if (entry.status !== 'draft') {
			void loadVersionHistory(entry, requestId);
		}
		try {
			let loadedModel: BuilderModel | null = null;
			if (entry.status === 'draft') {
				const draft = await fetchDraftVersion(entry.strategy_id, 1);
				loadedModel = toBuilderModel(draft.strategy, draft.revision);
			} else if (entry.latest_fingerprint) {
				const source = await fetchStrategySource(entry.latest_fingerprint);
				loadedModel = toBuilderModel(source, 0);
			} else {
				if (requestId === viewRequestId) {
					viewError = 'No immutable evidence is available for this strategy.';
				}
			}
			if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
			viewModel = loadedModel;
			if (loadedModel !== null) {
				// Datasets may have loaded first; refresh window defaults with the real warmup.
				applyLaunchWindowDefaults();
				void loadVersionResults(entry, requestId);
			}
		} catch (caught) {
			if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
			viewError = caught instanceof Error ? caught.message : 'Could not load strategy details.';
		} finally {
			if (requestId === viewRequestId && viewEntry?.strategy_id === entry.strategy_id) {
				viewLoading = false;
			}
		}
	}

	function closeView(): void {
		viewRequestId += 1;
		viewEntry = null;
		viewModel = null;
		viewError = null;
		versionHistory = null;
		historyError = null;
		reviseError = null;
		revising = null;
		diffCache = {};
		selectedStrategyFingerprint = '';
		launchDatasetError = null;
		launchDatasetsLoading = false;
	}

	async function loadLibrary(): Promise<void> {
		loading = true;
		error = null;
		try {
			entries = await listStrategies();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not load the strategy library.';
		} finally {
			loading = false;
		}
	}

	async function createNew(): Promise<void> {
		if (pendingAction) return;
		pendingAction = 'create';
		error = null;
		try {
			await createDraft();
			await loadLibrary();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not create a strategy draft.';
		} finally {
			pendingAction = null;
		}
	}

	async function clone(entry: StrategyLibraryEntry): Promise<void> {
		if (pendingAction || !entry.latest_fingerprint) return;
		pendingAction = `clone:${entry.strategy_id}`;
		error = null;
		try {
			await clonePublishedStrategy(entry.latest_fingerprint);
			await loadLibrary();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not clone the strategy.';
		} finally {
			pendingAction = null;
		}
	}

	async function archive(entry: StrategyLibraryEntry): Promise<void> {
		if (pendingAction || !entry.latest_fingerprint) return;
		pendingAction = `archive:${entry.strategy_id}`;
		error = null;
		try {
			await archivePublishedStrategy(entry.latest_fingerprint);
			await loadLibrary();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not archive the strategy.';
		} finally {
			pendingAction = null;
		}
	}

	function openImport(): void {
		importHint = null;
		importText = '';
		showImport = true;
	}

	function closeImport(): void {
		showImport = false;
		importText = '';
		importHint = null;
	}

	async function runImport(): Promise<void> {
		if (pendingAction) return;
		pendingAction = 'import';
		importHint = null;
		error = null;
		try {
			const parsed: unknown = JSON.parse(importText);
			await importStrategy(parsed);
			closeImport();
			await loadLibrary();
		} catch (caught) {
			if (caught instanceof SyntaxError) {
				importHint = 'That is not valid JSON. Paste a complete strategy definition.';
			} else {
				importHint =
					caught instanceof Error ? caught.message : 'Could not import the strategy definition.';
			}
		} finally {
			pendingAction = null;
		}
	}

	function formatReturn(entry: StrategyLibraryEntry): string {
		if (!entry.backtest) return '—';
		return formatPercent(entry.backtest.summary.total_return_fraction);
	}

	function formatDate(value: string): string {
		return new Date(value).toLocaleDateString();
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

	onMount(() => void loadLibrary());
</script>

<svelte:head><title>Strategies · ThyTrader</title></svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home"
			><span class="brand-mark">T</span><span>ThyTrader</span></a
		>
		<nav aria-label="Primary navigation">
			<a href={resolve('/')}>Portfolio</a>
			<a class="active" href={resolve('/strategies')}>Strategies</a>
			<a href={resolve('/backtests')}>Backtests</a>
			<a href={resolve('/audit')}>Audit</a>
		</nav>
		<div class="local-pill"><span></span> Research only</div>
	</header>
	<main>
		<section class="hero">
			<div>
				<p class="eyebrow">Conservative research</p>
				<h1>Strategy library</h1>
				<p class="lede">
					Every strategy identity with its latest evidence. Nothing here deploys or trades.
				</p>
			</div>
		</section>
		<section class="top-actions" aria-label="Library actions">
			<button class="refresh" type="button" onclick={createNew} disabled={pendingAction !== null}
				>{pendingAction === 'create' ? 'Creating…' : 'New strategy'}</button
			>
			<button class="secondary" type="button" onclick={openImport}>Import JSON…</button>
		</section>
		{#if error}<div class="error-banner" role="alert">
				<div>
					<strong>Research operation unavailable</strong>
					<p>{error}</p>
				</div>
				<button type="button" onclick={loadLibrary}>Retry library load</button>
			</div>{/if}
		<section class="library-card" aria-label="Strategy library">
			{#if loading}
				<div class="loading-region"><div class="skeleton wide"></div></div>
			{:else if entries.length === 0}
				<div class="empty-state">
					<p>No strategies yet.</p>
					<p class="empty-hint">
						Use <strong>New strategy</strong> above to create a conservative reference draft, or import
						a strategy definition you exported elsewhere.
					</p>
				</div>
			{:else}
				<div class="table-scroll">
					<table>
						<thead>
							<tr>
								<th scope="col">Name</th>
								<th scope="col">Market / timeframe</th>
								<th scope="col">Latest version</th>
								<th scope="col">Status</th>
								<th scope="col">Latest backtest</th>
								<th scope="col">Paper / live</th>
							</tr>
						</thead>
						<tbody>
							{#each entries as entry (entry.strategy_id)}
								<tr
									data-strategy-id={entry.strategy_id}
									class:hover-row={hoveredId === entry.strategy_id}
									onmouseenter={(event) => showBar(event, entry)}
									onmouseleave={scheduleHide}
									onclick={(event) => {
										if ((event.target as HTMLElement).closest('a, button')) return;
										openView(entry);
									}}
								>
									<td>
										<span class="strategy-name">{entry.name}</span>
									</td>
									<td>{entry.product_id} · {entry.timeframe}</td>
									<td>
										{entry.latest_version ?? '—'}{#if entry.latest_fingerprint}
											<code class="fingerprint">{entry.latest_fingerprint.slice(0, 18)}…</code>{/if}
									</td>
									<td>
										<span class="status-pill" data-status={entry.status}>{entry.status}</span>
									</td>
									<td>
										{#if entry.backtest}
											<a
												href={resolve(
													`/backtests?result=${encodeURIComponent(entry.backtest.result_fingerprint)}`
												)}
											>
												{formatReturn(entry)} · {entry.backtest.summary.trade_count} trades ·
												{formatDate(entry.backtest.published_at)}
											</a>
										{:else}
											<span class="muted">None yet</span>
										{/if}
									</td>
									<td>
										<span class="muted">{entry.paper_live.paper} / {entry.paper_live.live}</span>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	</main>
</div>

{#if showImport}
	<div class="import-backdrop">
		<div class="import-dialog" role="dialog" aria-modal="true" aria-label="Import strategy JSON">
			<h2>Import strategy JSON</h2>
			<p class="import-lede">
				Paste one complete strategy definition. It is stored as a new editable draft.
			</p>
			<textarea
				bind:value={importText}
				rows={12}
				spellcheck="false"
				aria-label="Strategy definition JSON"></textarea>
			{#if importHint}<p class="import-hint" role="alert">{importHint}</p>{/if}
			<div class="import-actions">
				<button
					class="secondary"
					type="button"
					onclick={closeImport}
					disabled={pendingAction !== null}>Cancel</button
				>
				<button
					class="refresh"
					type="button"
					onclick={runImport}
					disabled={pendingAction !== null || importText.trim().length === 0}
					>{pendingAction === 'import' ? 'Importing…' : 'Import draft'}</button
				>
			</div>
		</div>
	</div>
{/if}

{#if viewEntry}
	<div class="import-backdrop">
		<div class="view-drawer" role="dialog" aria-modal="true" aria-label="Strategy inspector">
			<div class="view-head">
				<div>
					<p class="eyebrow">{viewEntry.status} · v{viewEntry.latest_version ?? '—'}</p>
					<h2>{viewEntry.name}</h2>
				</div>
				<button class="secondary" type="button" onclick={closeView}>Close</button>
			</div>
			<div class="drawer-tabs" role="tablist" aria-label="Strategy detail sections">
				<button
					class="drawer-tab"
					class:active={researchTab === 'insight'}
					type="button"
					role="tab"
					aria-selected={researchTab === 'insight'}
					onclick={() => (researchTab = 'insight')}>Insight</button
				>
				<button
					class="drawer-tab"
					class:active={researchTab === 'research'}
					type="button"
					role="tab"
					aria-selected={researchTab === 'research'}
					onclick={() => (researchTab = 'research')}>Research</button
				>
				<button
					class="drawer-tab"
					class:active={researchTab === 'versions'}
					type="button"
					role="tab"
					aria-selected={researchTab === 'versions'}
					onclick={() => (researchTab = 'versions')}>Versions</button
				>
			</div>
			{#if viewLoading}
				<p class="view-note">Loading strategy evidence…</p>
			{:else if viewError}
				<p class="view-problem" role="alert">{viewError}</p>
			{:else if viewModel && researchTab === 'insight'}
				<div class="view-block">
					<h3>Plain-English summary</h3>
					<p>{plainEnglishSummary(viewModel)}</p>
				</div>
				<div class="view-block">
					<h3>Validation</h3>
					{#if validateDefinition(viewModel).length === 0}
						<p class="view-ok">No problems detected.</p>
					{:else}
						<ul class="view-problems">
							{#each validateDefinition(viewModel) as problem (problem)}
								<li>{problem}</li>
							{/each}
						</ul>
					{/if}
				</div>
				<div class="view-block">
					<h3>Required data</h3>
					<p>
						{viewModel.warmup_bars} completed {viewModel.timeframe} bars (OHLCV) before the first signal.
					</p>
				</div>
				<div class="view-block">
					<h3>Unsaved changes</h3>
					<p class="view-ok">Read-only — no local edits.</p>
				</div>
				<div class="view-block">
					<h3>Engine support</h3>
					<EngineSupportMatrix />
				</div>
			{:else if viewModel && researchTab === 'research'}
				<div class="view-block">
					<h3>Launch backtest</h3>
					<p class="view-note">
						Runs against the selected immutable version of this strategy. Results are deterministic
						and reproducible.
					</p>
					{#if publishedVersionsFor(viewEntry).length === 0}
						<p class="view-note">Publish this draft before launching a reproducible backtest.</p>
					{:else}
						<div class="launch-grid">
							<label
								>Strategy version
								<select bind:value={selectedStrategyFingerprint}>
									{#each publishedVersionsFor(viewEntry) as version (version.strategy_fingerprint)}
										<option value={version.strategy_fingerprint}>Version {version.version}</option>
									{/each}
								</select></label
							>
							<label
								>Verified dataset
								<select
									bind:value={launchForm.dataset_fingerprint}
									onchange={() => applyLaunchWindowDefaults()}
								>
									<option value="">Select a verified {viewEntry.product_id} dataset</option>
									{#each launchDatasets as dataset (dataset.content_fingerprint)}
										<option value={dataset.content_fingerprint}
											>{new Date(dataset.starts_at).toLocaleDateString()} – {new Date(
												dataset.ends_at
											).toLocaleDateString()}</option
										>
									{/each}
								</select>
								{#if launchDatasetsLoading}
									<small class="field-note">Loading verified datasets…</small>
								{:else if launchDatasetError}
									<small class="field-error" role="alert">{launchDatasetError}</small>
								{:else if launchDatasets.length === 0}
									<small class="field-note">No verified datasets match this market.</small>
								{/if}</label
							>
						</div>
						<div class="launch-grid">
							<label
								>Engine
								<select bind:value={launchForm.engine}>
									<option value="thytrader-bar-backtest-v1">V1 — mark price, fixed slippage</option>
									<option value="thytrader-bar-backtest-v2">V2 — constant spread (bid/ask)</option>
								</select></label
							>
							{#if launchForm.engine === 'thytrader-bar-backtest-v2'}
								<label
									>Constant spread (bps, total bid-ask)
									<input inputmode="decimal" bind:value={launchForm.spread_bps} /></label
								>
							{/if}
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
						<div class="launch-grid">
							<label
								>Initial capital (USD)
								<input inputmode="decimal" bind:value={launchForm.initial_quote_balance} /></label
							>
							<label
								>Maker fee rate
								<input inputmode="decimal" bind:value={launchForm.maker_fee_rate} /></label
							>
						</div>
						<div class="launch-grid">
							<label
								>Taker fee rate
								<input inputmode="decimal" bind:value={launchForm.taker_fee_rate} /></label
							>
							<label
								>Fixed slippage (bps)
								<input inputmode="decimal" bind:value={launchForm.fixed_slippage_bps} /></label
							>
						</div>
						{#if launchError}<p class="view-problem" role="alert">{launchError}</p>{/if}
						<button
							class="refresh launch-button"
							type="button"
							onclick={runLaunch}
							disabled={launching ||
								selectedStrategyFingerprint === '' ||
								launchForm.dataset_fingerprint === '' ||
								launchForm.evaluation_start === '' ||
								launchForm.evaluation_end === ''}
						>
							{launching ? 'Running simulation…' : 'Run backtest'}
						</button>
					{/if}
				</div>
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
										<td>{row.engine_contract_version.endsWith('-v2') ? 'V2' : 'V1'}</td>
										<td>
											<a
												href={resolve(
													`/backtests?result=${encodeURIComponent(row.result_fingerprint)}`
												)}>{formatPercent(row.total_return_fraction)}</a
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
						<p class="view-note">
							This strategy has no immutable published versions to compare yet.
						</p>
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
													<td>{row.engine_contract_version.endsWith('-v2') ? 'V2' : 'V1'}</td>
													<td>{row.trade_count}</td>
													<td>{formatPercent(row.win_rate)}</td>
													<td>{formatPercent(row.maximum_drawdown_fraction)}</td>
													<td>{new Date(row.published_at).toLocaleString()}</td>
												</tr>
											{/each}
										</tbody>
									</table>
								{/if}
							</div>
						{/each}
					{/if}
				</div>
			{:else if viewModel && researchTab === 'versions'}
				{@const viewSnapshot = viewEntry}
				{@const historySnapshot = versionHistory}
				{#if viewSnapshot.status === 'draft'}
					<p class="view-note">
						Publish this draft to start an immutable version history. Cloning creates a separate
						strategy identity instead.
					</p>
				{:else if historyLoading}
					<p class="view-note">Loading version history…</p>
				{:else if historyError}
					<p class="view-problem" role="alert">{historyError}</p>
				{:else if reviseError}
					<p class="view-problem" role="alert">{reviseError}</p>
				{:else if historySnapshot}
					<div class="view-block">
						<h3>Published versions</h3>
						{#if historySnapshot.versions.length === 0}
							<p class="view-note">No immutable published versions yet.</p>
						{:else}
							<table class="results-table" aria-label="Published version history">
								<thead>
									<tr>
										<th scope="col">Version</th>
										<th scope="col">Fingerprint</th>
										<th scope="col">Status</th>
										<th scope="col">Latest backtest</th>
										<th scope="col">Actions</th>
									</tr>
								</thead>
								<tbody>
									{#each historySnapshot.versions as version (version.version)}
										{@const latestFingerprint =
											historySnapshot.versions[historySnapshot.versions.length - 1]
												.strategy_fingerprint}
										<tr>
											<td>V{version.version}</td>
											<td
												><code class="fingerprint"
													>{version.strategy_fingerprint.slice(0, 18)}…</code
												></td
											>
											<td>
												{version.archived
													? `archived${version.archived_at ? ` · ${new Date(version.archived_at).toLocaleDateString()}` : ''}`
													: 'active'}
											</td>
											<td>
												{#if version.backtest}
													<a
														href={resolve(
															`/backtests?result=${encodeURIComponent(version.backtest.result_fingerprint)}`
														)}>{formatPercent(version.backtest.summary.total_return_fraction)}</a
													>
												{:else}
													<span class="muted">None</span>
												{/if}
											</td>
											<td>
												<div class="version-actions">
													<button
														class="bar-button"
														type="button"
														disabled={revising !== null}
														onclick={() =>
															reviseFromVersion(viewSnapshot, version.strategy_fingerprint)}
													>
														{revising === version.strategy_fingerprint
															? 'Creating…'
															: 'Edit into next draft'}
													</button>
													<button
														class="bar-button"
														type="button"
														onclick={() =>
															exportVersion(
																viewSnapshot,
																version.strategy_fingerprint,
																version.version
															)}
													>
														Export
													</button>
													<button
														class="bar-button"
														type="button"
														disabled={revising !== null ||
															version.strategy_fingerprint === latestFingerprint}
														onclick={() =>
															(diffSelection = {
																from: version.version,
																to: historySnapshot.versions[historySnapshot.versions.length - 1]
																	.version
															})}
													>
														Compare to latest
													</button>
												</div>
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						{/if}
					</div>
					{#if historySnapshot.draft}
						<div class="view-block">
							<h3>Open draft</h3>
							<p class="view-note">
								A draft v{historySnapshot.draft.strategy.version} already exists for this strategy.
							</p>
							<a
								class="secondary view-edit"
								href={resolve(`/strategies/${viewSnapshot.strategy_id}`)}
								>Open draft v{historySnapshot.draft.strategy.version}</a
							>
						</div>
					{/if}
					{#if historySnapshot.versions.length >= 2}
						<div class="view-block">
							<h3>Semantic diff</h3>
							<div class="launch-grid">
								<label
									>From version
									<select bind:value={diffSelection.from}>
										{#each historySnapshot.versions as version (version.version)}
											<option value={version.version}>V{version.version}</option>
										{/each}
									</select></label
								>
								<label
									>To version
									<select bind:value={diffSelection.to}>
										{#each historySnapshot.versions as version (version.version)}
											<option value={version.version}>V{version.version}</option>
										{/each}
									</select></label
								>
							</div>
							{#await currentDiff()}
								<p class="view-note">Comparing versions…</p>
							{:then diff}
								{#if diff === null || diff.changes.length === 0}
									<p class="view-note">These versions are semantically equivalent.</p>
								{:else}
									<p class="view-note">{diff.summary}</p>
									<table class="results-table diff-table" aria-label="Semantic diff">
										<thead>
											<tr>
												<th scope="col">Field</th>
												<th scope="col">From</th>
												<th scope="col">To</th>
											</tr>
										</thead>
										<tbody>
											{#each diff.changes as change (change.path + change.kind)}
												<tr>
													<td>{change.label}</td>
													<td><code>{change.from === '' ? '—' : change.from}</code></td>
													<td><code>{change.to === '' ? '—' : change.to}</code></td>
												</tr>
											{/each}
										</tbody>
									</table>
								{/if}
							{:catch}
								<p class="view-problem" role="alert">Could not load the selected versions.</p>
							{/await}
						</div>
					{/if}
				{/if}
				{#if viewEntry.status === 'draft' && viewEntry.strategy_id}
					<a class="secondary view-edit" href={resolve(`/strategies/${viewEntry.strategy_id}`)}
						>Edit this draft</a
					>
				{/if}
			{/if}
		</div>
	</div>
{/if}

{#if hoveredId !== null && barPosition !== null}
	<div
		class="hover-actions"
		bind:clientWidth={barWidth}
		bind:clientHeight={barHeight}
		style="left: {barPosition.x}px; top: {barPosition.y}px;"
		onmouseenter={cancelHide}
		onmouseleave={scheduleHide}
		role="toolbar"
		aria-label="Row actions"
		tabindex="-1"
	>
		{#if hoveredId !== null}
			{@const entry = entries.find((candidate) => candidate.strategy_id === hoveredId)}
			{#if entry}
				<button class="bar-button" type="button" onclick={() => openView(entry)}>View</button>
				{#if entry.status === 'draft'}
					<a class="bar-button" href={resolve(`/strategies/${entry.strategy_id}`)}>Edit</a>
				{/if}
				{#if entry.latest_fingerprint}
					<button
						class="bar-button"
						type="button"
						disabled={pendingAction !== null}
						onclick={() => clone(entry)}
					>
						{pendingAction === `clone:${entry.strategy_id}` ? 'Cloning…' : 'Clone'}
					</button>
					<button
						class="bar-button bar-danger"
						type="button"
						disabled={pendingAction !== null}
						onclick={() => archive(entry)}
					>
						{pendingAction === `archive:${entry.strategy_id}` ? 'Archiving…' : 'Archive'}
					</button>
				{/if}
			{/if}
		{/if}
	</div>
{/if}

<style>
	.library-card {
		background: var(--card, #141b1c);
		border: 1px solid #303a3c;
		border-radius: 12px;
		overflow: hidden;
		margin-bottom: 24px;
	}
	.loading-region {
		padding: 24px;
	}
	.table-scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}
	th,
	td {
		text-align: left;
		padding: 14px 16px;
		border-bottom: 1px solid #232d2e;
		vertical-align: top;
	}
	th {
		color: #aeb9bb;
		font-weight: 500;
		font-size: 12px;
		white-space: nowrap;
	}
	tbody tr:last-child td {
		border-bottom: none;
	}
	tbody tr {
		cursor: default;
	}
	tbody tr.hover-row td {
		background: #1b2527;
	}
	tbody tr.hover-row td:first-child {
		box-shadow: inset 3px 0 0 #2f6f52;
	}
	.strategy-name {
		display: block;
		color: #edf3f3;
		font-weight: 600;
	}
	.fingerprint {
		display: block;
		font-size: 10px;
		color: #77888b;
		margin-top: 3px;
		word-break: break-all;
	}
	.status-pill {
		display: inline-block;
		border-radius: 999px;
		padding: 3px 10px;
		font-size: 11px;
		background: #1d2627;
		color: #d8e1e2;
		border: 1px solid #3a4648;
	}
	.status-pill[data-status='published'] {
		color: #83d5a3;
		border-color: #2f5c44;
	}
	.status-pill[data-status='archived'] {
		color: #9aa8aa;
		border-color: #38444a;
	}
	.muted {
		color: #77888b;
	}
	td a {
		color: #7fd0f0;
	}
	.hover-actions {
		position: fixed;
		z-index: 30;
		display: flex;
		gap: 4px;
		padding: 5px;
		background: rgba(24, 32, 33, 0.92);
		backdrop-filter: blur(6px);
		border: 1px solid #3a4648;
		border-radius: 10px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
		transition: opacity 100ms ease;
	}
	.bar-button {
		border: 1px solid #455457;
		border-radius: 8px;
		background: transparent;
		color: #d8e1e2;
		padding: 6px 11px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
		text-decoration: none;
		display: inline-block;
	}
	.bar-button:hover:not(:disabled) {
		background: #273437;
		border-color: #5b6c70;
	}
	.bar-button.bar-danger:hover:not(:disabled) {
		color: #f0a3a3;
		border-color: #6c4040;
	}
	.bar-button:disabled {
		opacity: 0.55;
		cursor: default;
	}
	.secondary {
		border: 1px solid #455457;
		border-radius: 8px;
		background: transparent;
		color: #d8e1e2;
		padding: 8px 10px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
	}
	.refresh {
		border: none;
		border-radius: 8px;
		background: #2f6f52;
		color: #eafff3;
		padding: 10px 14px;
		font: inherit;
		font-size: 13px;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.55;
		cursor: default;
	}
	.top-actions {
		display: flex;
		gap: 12px;
		margin-bottom: 16px;
	}
	.empty-state {
		padding: 42px 24px;
		text-align: center;
		color: #d8e1e2;
	}
	.empty-state p {
		margin: 0 0 8px;
	}
	.empty-hint {
		color: #aeb9bb;
		font-size: 13px;
	}
	.import-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(5, 9, 10, 0.72);
		display: grid;
		place-items: center;
		padding: 24px;
		z-index: 20;
	}
	.import-dialog {
		width: min(680px, 100%);
		background: #141b1c;
		border: 1px solid #303a3c;
		border-radius: 12px;
		padding: 22px 24px;
	}
	.import-dialog h2 {
		margin: 0 0 6px;
		font-size: 18px;
		color: #edf3f3;
	}
	.import-lede {
		margin: 0 0 14px;
		color: #aeb9bb;
		font-size: 13px;
	}
	textarea {
		width: 100%;
		border: 1px solid #303a3c;
		border-radius: 8px;
		background: #101617;
		color: #edf3f3;
		padding: 12px;
		font-family: ui-monospace, monospace;
		font-size: 12px;
		resize: vertical;
	}
	.import-hint {
		color: #f0a3a3;
		font-size: 12px;
		margin: 10px 0 0;
	}
	.import-actions {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		margin-top: 14px;
	}
	.view-drawer {
		width: min(920px, 100%);
		max-height: 86vh;
		overflow-y: auto;
		background: #141b1c;
		border: 1px solid #303a3c;
		border-radius: 12px;
		padding: 20px 24px;
		display: grid;
		gap: 16px;
	}
	.view-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 12px;
	}
	.view-head h2 {
		margin: 2px 0 0;
		font-size: 18px;
		color: #edf3f3;
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
	.view-ok {
		color: #83d5a3;
		font-size: 13px;
	}
	.view-problem {
		color: #f0a3a3;
		font-size: 13px;
	}
	.view-problems {
		margin: 0;
		padding-left: 16px;
		color: #f0a3a3;
		font-size: 12px;
		display: grid;
		gap: 4px;
	}
	.version-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.view-edit {
		justify-self: start;
		text-decoration: none;
	}

	.drawer-tabs {
		display: flex;
		gap: 6px;
	}
	.drawer-tab {
		border: 1px solid #303a3c;
		background: transparent;
		color: #aeb9bb;
		border-radius: 999px;
		padding: 6px 14px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
	}
	.drawer-tab.active {
		background: #1d2b26;
		color: #9fe0bd;
		border-color: #2f5c44;
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
	.launch-button {
		justify-self: start;
		border: none;
		border-radius: 8px;
		background: #2f6f52;
		color: #eafff3;
		padding: 9px 14px;
		font: inherit;
		font-size: 13px;
		cursor: pointer;
	}
	.launch-button:disabled {
		opacity: 0.55;
		cursor: default;
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
	@media (max-width: 900px) {
		table {
			font-size: 12px;
		}
	}
	@media (max-width: 640px) {
		.launch-grid {
			grid-template-columns: 1fr;
		}
		.view-drawer {
			padding: 16px;
		}
	}
</style>
