<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { formatPercent } from '$lib/backtests';
	import EngineSupportMatrix from '$lib/EngineSupportMatrix.svelte';
	import {
		archiveConfirmMessage,
		archivePublishedStrategy,
		createDraft,
		clonePublishedStrategy,
		EXECUTION_TIMEFRAMES,
		fetchDraftVersion,
		fetchStrategyHistory,
		fetchStrategySource,
		formatUtcInputValue,
		importStrategy,
		listStrategies,
		PAPER_LIVE_STATUS_LEGEND,
		PAPER_LIVE_STATUS_TITLE,
		paperLiveStatusLabel,
		paperLiveStatusTitle,
		reviseStrategy,
		toBuilderModel,
		type BuilderModel,
		type StrategyLibraryEntry,
		type StrategyVersionHistory
	} from '$lib/strategies';
	import { semanticDiff, type SemanticDiff } from '$lib/strategy-diff';
	import { plainEnglishSummary, requiredDataText, validateDefinition } from '$lib/strategy-insight';

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
	let researchTab = $state<'insight' | 'versions'>('insight');
	let draftTemplate = $state('ema-trend');
	let draftTimeframe = $state('1h');
	let versionHistory = $state<StrategyVersionHistory | null>(null);
	let historyLoading = $state(false);
	let historyError = $state<string | null>(null);
	let revising = $state<string | null>(null);
	let reviseError = $state<string | null>(null);
	let exporting = $state(false);
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

	type SemanticDiffView =
		| { status: 'same-version' }
		| { status: 'unavailable' }
		| { status: 'ready'; diff: SemanticDiff };

	async function currentDiff(): Promise<SemanticDiffView> {
		if (!viewEntry || !versionHistory) return { status: 'unavailable' };
		const fromVersion = versionHistory.versions.find(
			(version) => version.version === Number(diffSelection.from)
		);
		const toVersion = versionHistory.versions.find(
			(version) => version.version === Number(diffSelection.to)
		);
		if (fromVersion === undefined || toVersion === undefined) return { status: 'unavailable' };
		if (Number(diffSelection.from) === Number(diffSelection.to)) {
			return { status: 'same-version' };
		}
		try {
			const [before, after] = await Promise.all([
				loadDiffModel(viewEntry, fromVersion.strategy_fingerprint),
				loadDiffModel(viewEntry, toVersion.strategy_fingerprint)
			]);
			return { status: 'ready', diff: semanticDiff(before, after) };
		} catch {
			return { status: 'unavailable' };
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
			const [library, history] = await Promise.all([
				listStrategies().catch(() => null),
				fetchStrategyHistory(entry.strategy_id).catch(() => null)
			]);
			if (library !== null) entries = library;
			const revised = history?.draft ?? null;
			const [updatedEntry] = (library ?? []).filter(
				(candidate) => candidate.strategy_id === entry.strategy_id
			);
			if (history !== null) {
				versionHistory = history;
				if (revised !== null && viewEntry?.strategy_id === entry.strategy_id) {
					viewEntry = { ...(updatedEntry ?? entry) };
					viewModel = toBuilderModel(revised.strategy, revised.revision);
				}
			}
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
		if (exporting) return;
		exporting = true;
		historyError = null;
		try {
			const source = await fetchStrategySource(fingerprint);
			const blob = new Blob([JSON.stringify(source, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = versionHistoryDownloadName(entry, version);
			anchor.click();
			URL.revokeObjectURL(url);
		} catch (caught) {
			historyError =
				caught instanceof Error ? caught.message : 'Could not export the strategy definition.';
		} finally {
			exporting = false;
		}
	}

	async function openView(
		entry: StrategyLibraryEntry,
		tab: 'insight' | 'versions' = 'insight'
	): Promise<void> {
		const requestId = ++viewRequestId;
		viewEntry = entry;
		viewModel = null;
		viewError = null;
		viewLoading = true;
		researchTab = tab;
		if (entry.published_versions.length > 0 || entry.status !== 'draft') {
			void loadVersionHistory(entry, requestId);
		}
		try {
			let loadedModel: BuilderModel | null = null;
			if (entry.status === 'draft') {
				const draftVersion = entry.latest_version ?? 1;
				const draft = await fetchDraftVersion(entry.strategy_id, draftVersion);
				loadedModel = toBuilderModel(draft.strategy, draft.revision);
			} else if (entry.latest_fingerprint) {
				const source = await fetchStrategySource(entry.latest_fingerprint);
				loadedModel = toBuilderModel(source, 0);
			} else if (requestId === viewRequestId) {
				viewError = 'No immutable evidence is available for this strategy.';
			}
			if (requestId !== viewRequestId || viewEntry?.strategy_id !== entry.strategy_id) return;
			viewModel = loadedModel;
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
			await createDraft({ template: draftTemplate, timeframe: draftTimeframe });
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
		const confirmed = window.confirm(
			archiveConfirmMessage({
				name: entry.name,
				latest_version: entry.latest_version,
				latest_fingerprint: entry.latest_fingerprint
			})
		);
		if (!confirmed) return;
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
		return formatUtcInputValue(new Date(value)).replace('T', ' ');
	}

	onMount(() => void loadLibrary());
</script>

<svelte:head><title>Strategies · ThyTrader</title></svelte:head>

<main>
	<section class="hero">
		<div>
			<p class="eyebrow">Conservative research</p>
			<h1>Strategy library</h1>
			<p class="lede">
				Create and inspect strategies here. Launch research from Research, and paper or live from
				Deploy. This drawer stays on Insight and Versions.
			</p>
		</div>
	</section>
	<section class="top-actions" aria-label="Library actions">
		<button class="refresh" type="button" onclick={createNew} disabled={pendingAction !== null}
			>{pendingAction === 'create' ? 'Creating…' : 'New strategy'}</button
		>
		<label class="template-picker"
			>Template
			<select bind:value={draftTemplate} disabled={pendingAction !== null}>
				<option value="ema-trend">EMA trend</option>
				<option value="rsi-mean-reversion">RSI mean reversion</option>
				<option value="macd-trend">MACD trend</option>
				<option value="bollinger-mean-reversion">Bollinger mean reversion</option>
			</select></label
		>
		<label class="template-picker"
			>Clock
			<select bind:value={draftTimeframe} disabled={pendingAction !== null}>
				{#each EXECUTION_TIMEFRAMES as clock (clock)}
					<option value={clock}>{clock}</option>
				{/each}
			</select></label
		>
		<button class="secondary" type="button" onclick={openImport}>Import JSON…</button>
	</section>
	{#if error}<div class="error-banner" role="alert">
			<div>
				<strong>Strategy library unavailable</strong>
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
							<th class="paper-live-col" scope="col" title={PAPER_LIVE_STATUS_TITLE}>
								Paper / live
								<span class="col-legend">{PAPER_LIVE_STATUS_LEGEND}</span>
							</th>
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
									<a
										class="paper-live-status"
										href={resolve(`/deploy?strategy=${encodeURIComponent(entry.strategy_id)}`)}
										title={paperLiveStatusTitle(entry.paper_live)}
									>
										{paperLiveStatusLabel(entry.paper_live)}
									</a>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>
</main>

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
				<a
					class="drawer-tab"
					href={resolve(`/research?strategy=${encodeURIComponent(viewEntry.strategy_id)}`)}
					>Research</a
				>
				<button
					class="drawer-tab"
					class:active={researchTab === 'versions'}
					type="button"
					role="tab"
					aria-selected={researchTab === 'versions'}
					onclick={() => (researchTab = 'versions')}>Versions</button
				>
				<a
					class="drawer-tab"
					href={resolve(`/deploy?strategy=${encodeURIComponent(viewEntry.strategy_id)}`)}>Deploy</a
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
						{requiredDataText(viewModel)}
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
			{:else if viewModel && researchTab === 'versions'}
				{@const viewSnapshot = viewEntry}
				{@const historySnapshot = versionHistory}
				{#if historyLoading}
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
													? `archived${version.archived_at ? ` · ${formatUtcInputValue(new Date(version.archived_at)).slice(0, 10)}` : ''}`
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
														disabled={exporting}
														onclick={() =>
															exportVersion(
																viewSnapshot,
																version.strategy_fingerprint,
																version.version
															)}
													>
														{exporting ? 'Exporting…' : 'Export'}
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
							{:then result}
								{#if result.status === 'same-version'}
									<p class="view-note">Select two different versions to compare.</p>
								{:else if result.status === 'unavailable'}
									<p class="view-problem" role="alert">
										Could not load the selected versions for comparison.
									</p>
								{:else if result.status === 'ready' && result.diff.changes.length === 0}
									<p class="view-note">These versions are semantically equivalent.</p>
								{:else if result.status === 'ready'}
									<p class="view-note">{result.diff.summary}</p>
									<table class="results-table diff-table" aria-label="Semantic diff">
										<thead>
											<tr>
												<th scope="col">Field</th>
												<th scope="col">From</th>
												<th scope="col">To</th>
											</tr>
										</thead>
										<tbody>
											{#each result.diff.changes as change (change.path + change.kind)}
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
				<a
					class="bar-button"
					href={resolve(`/research?strategy=${encodeURIComponent(entry.strategy_id)}`)}>Research</a
				>
				<a
					class="bar-button"
					href={resolve(`/deploy?strategy=${encodeURIComponent(entry.strategy_id)}`)}>Deploy</a
				>
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
	th.paper-live-col {
		white-space: normal;
	}
	.col-legend {
		display: block;
		margin-top: 4px;
		color: #77888b;
		font-size: 10px;
		font-weight: 400;
		white-space: nowrap;
	}
	.paper-live-status {
		appearance: none;
		border: 0;
		background: transparent;
		color: #77888b;
		font: inherit;
		padding: 0;
		cursor: pointer;
		text-align: left;
		text-decoration: none;
	}
	.paper-live-status:hover,
	.paper-live-status:focus-visible {
		color: #d8e1e2;
		text-decoration: underline;
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
		align-items: end;
		flex-wrap: wrap;
	}
	.template-picker {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 12px;
		color: #aeb9bb;
	}
	.template-picker select {
		min-height: 36px;
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
		text-decoration: none;
		display: inline-flex;
		align-items: center;
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
