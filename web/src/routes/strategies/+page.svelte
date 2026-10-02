<script lang="ts">
	/**
	 * Strategy library (ADR 0080, ADR 0082). Each row opens that strategy's
	 * workspace and shows its evidence pipeline — Build / Test / Paper / Live
	 * chips derived from the library row payload, never a readiness verdict.
	 *
	 * Create, import, clone, and delete live here. Delete (one row or a
	 * checkbox selection) first asks the server for a dry run, so the
	 * accessible confirmation lists exactly what goes, which strategies are
	 * blocked by running or paused bots, and that stopped live history is kept.
	 * Results are shown per strategy, including partial failures.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { compareDecimalStrings, formatPercent } from '$lib/backtests';
	import { marketLabel } from '$lib/deployment-detail';
	import {
		bulkDeleteStrategies,
		bulkOutcomeText,
		cloneStrategy,
		createStrategy,
		deleteStrategy,
		deletionCountsText,
		EXECUTION_TIMEFRAMES,
		fetchStrategyPage,
		formatUtcInputValue,
		importStrategy,
		STRATEGY_TEMPLATE_OPTIONS,
		strategyErrorCode,
		StrategyApiError,
		type BulkDeleteItem,
		type StrategyLibraryEntry
	} from '$lib/strategies';
	import {
		libraryPipeline,
		pipelineSummary,
		shortStrategyFingerprint,
		workspaceHref
	} from '$lib/strategy-workspace';

	let entries = $state<StrategyLibraryEntry[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let pageSize = $state<10 | 25 | 50 | 100>(10);
	/** Show only strategies whose metadata.tags include this tag (ADR 0094). */
	let tagFilter = $state<string | null>(null);
	let pageIndex = $state(0);
	let pageCursors = $state<(string | undefined)[]>([undefined]);
	let nextCursor = $state<string | null>(null);
	let libraryRequestId = 0;
	let pendingAction = $state<string | null>(null);
	let showImport = $state(false);
	let importText = $state('');
	let importHint = $state<string | null>(null);
	let draftTemplate = $state('ema-trend');
	let draftTimeframe = $state('1h');
	const PRODUCT_STORAGE_KEY = 'thytrader.newStrategyProduct';
	let draftProduct = $state(readStoredProduct());

	/** Last market used for New strategy (per viewer); BTC-USDC matches the default USDC policy. */
	function readStoredProduct(): string {
		try {
			return globalThis.localStorage?.getItem(PRODUCT_STORAGE_KEY) || 'BTC-USDC';
		} catch {
			return 'BTC-USDC';
		}
	}

	function rememberProduct(productId: string): void {
		try {
			globalThis.localStorage?.setItem(PRODUCT_STORAGE_KEY, productId);
		} catch {
			// Storage is a convenience only.
		}
	}

	/** Selected strategy ids on the current page. */
	let selected = $state<string[]>([]);
	/** Strategies the open delete dialog is about (one row or the selection). */
	let deleteTargets = $state<StrategyLibraryEntry[]>([]);
	let deletePreview = $state<BulkDeleteItem[] | null>(null);
	let deletePreviewLoading = $state(false);
	let deleteError = $state<string | null>(null);
	let deleting = $state(false);
	/** Per-strategy results of the last confirmed deletion. */
	let deleteResults = $state<BulkDeleteItem[] | null>(null);

	const allSelected = $derived(
		entries.length > 0 && entries.every((entry) => selected.includes(entry.strategy_id))
	);
	const someSelected = $derived(selected.length > 0 && !allSelected);
	const deletable = $derived(
		(deletePreview ?? []).filter((item) => item.outcome === 'would_delete')
	);
	const blockedCount = $derived(
		(deletePreview ?? []).filter((item) => item.outcome !== 'would_delete').length
	);

	async function loadLibrary(reset = true): Promise<void> {
		const requestId = ++libraryRequestId;
		if (reset) {
			pageIndex = 0;
			pageCursors = [undefined];
		}
		loading = true;
		error = null;
		try {
			const page = await fetchStrategyPage(pageSize, pageCursors[pageIndex], tagFilter);
			if (requestId !== libraryRequestId) return;
			if (page.entries.length === 0 && pageIndex > 0) {
				pageIndex -= 1;
				void loadLibrary(false);
				return;
			}
			entries = page.entries;
			nextCursor = page.nextCursor;
			const onPage = new Set(page.entries.map((entry) => entry.strategy_id));
			selected = selected.filter((id) => onPage.has(id));
		} catch (caught) {
			if (requestId !== libraryRequestId) return;
			entries = [];
			nextCursor = null;
			error = caught instanceof Error ? caught.message : 'Could not load the strategy library.';
		} finally {
			if (requestId === libraryRequestId) loading = false;
		}
	}

	function filterByTag(tag: string | null): void {
		tagFilter = tag;
		selected = [];
		void loadLibrary();
	}

	function changePageSize(event: Event): void {
		pageSize = Number((event.currentTarget as HTMLSelectElement).value) as typeof pageSize;
		selected = [];
		void loadLibrary();
	}

	function nextPage(): void {
		if (loading || nextCursor === null) return;
		pageCursors = [...pageCursors.slice(0, pageIndex + 1), nextCursor];
		pageIndex += 1;
		selected = [];
		void loadLibrary(false);
	}

	function previousPage(): void {
		if (loading || pageIndex === 0) return;
		pageIndex -= 1;
		selected = [];
		void loadLibrary(false);
	}

	function toggleAll(): void {
		selected = allSelected ? [] : entries.map((entry) => entry.strategy_id);
	}

	function toggleOne(strategyId: string): void {
		selected = selected.includes(strategyId)
			? selected.filter((id) => id !== strategyId)
			: [...selected, strategyId];
	}

	async function createNew(): Promise<void> {
		if (pendingAction) return;
		pendingAction = 'create';
		error = null;
		try {
			const productId = draftProduct.trim().toUpperCase();
			await createStrategy({
				template: draftTemplate,
				product_id: productId,
				timeframe: draftTimeframe
			});
			rememberProduct(productId);
			await loadLibrary();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not create a strategy.';
		} finally {
			pendingAction = null;
		}
	}

	async function clone(entry: StrategyLibraryEntry): Promise<void> {
		if (pendingAction) return;
		pendingAction = `clone:${entry.strategy_id}`;
		error = null;
		try {
			await cloneStrategy(entry.strategy_id);
			await loadLibrary();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not clone the strategy.';
		} finally {
			pendingAction = null;
		}
	}

	async function askDelete(targets: StrategyLibraryEntry[]): Promise<void> {
		if (targets.length === 0) return;
		deleteTargets = targets;
		deletePreview = null;
		deleteError = null;
		deletePreviewLoading = true;
		try {
			const preview = await bulkDeleteStrategies(
				targets.map((entry) => entry.strategy_id),
				{ dryRun: true }
			);
			deletePreview = preview.results;
		} catch (caught) {
			deleteError =
				caught instanceof Error ? caught.message : 'Could not preview what would be deleted.';
		} finally {
			deletePreviewLoading = false;
		}
	}

	function closeDelete(): void {
		if (deleting) return;
		deleteTargets = [];
		deletePreview = null;
		deleteError = null;
	}

	function nameOf(strategyId: string): string {
		return (
			deleteTargets.find((entry) => entry.strategy_id === strategyId)?.name ??
			entries.find((entry) => entry.strategy_id === strategyId)?.name ??
			strategyId
		);
	}

	async function deleteOne(strategyId: string): Promise<BulkDeleteItem> {
		try {
			const result = await deleteStrategy(strategyId);
			return {
				strategy_id: strategyId,
				name: result.name,
				outcome: 'deleted',
				code: null,
				message: null,
				deployment_ids: [],
				counts: result.counts
			};
		} catch (caught) {
			const code = strategyErrorCode(caught);
			const detail = caught instanceof StrategyApiError ? caught.detail : {};
			return {
				strategy_id: strategyId,
				name: nameOf(strategyId),
				outcome:
					code === 'strategy_has_active_deployments'
						? 'blocked'
						: code === 'strategy_not_found'
							? 'not_found'
							: 'failed',
				code,
				message: caught instanceof Error ? caught.message : null,
				deployment_ids: Array.isArray(detail.deployment_ids)
					? detail.deployment_ids.filter((id): id is string => typeof id === 'string')
					: [],
				counts: null
			};
		}
	}

	async function confirmDelete(): Promise<void> {
		if (deleting || deletable.length === 0) return;
		deleting = true;
		deleteError = null;
		try {
			const ids = deletable.map((item) => item.strategy_id);
			let results: BulkDeleteItem[];
			if (deleteTargets.length === 1 && ids.length === 1) {
				results = [await deleteOne(ids[0]!)];
			} else {
				results = (await bulkDeleteStrategies(ids, { dryRun: false })).results;
			}
			const skipped = (deletePreview ?? []).filter((item) => item.outcome !== 'would_delete');
			deleteResults = [...results, ...skipped];
			const removed = new Set(
				results.filter((item) => item.outcome === 'deleted').map((item) => item.strategy_id)
			);
			selected = selected.filter((id) => !removed.has(id));
			deleteTargets = [];
			deletePreview = null;
			await loadLibrary(false);
		} catch (caught) {
			deleteError = caught instanceof Error ? caught.message : 'Could not delete the strategies.';
		} finally {
			deleting = false;
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
			const created = await importStrategy(parsed);
			closeImport();
			await goto(resolve(workspaceHref(created.strategy_id, 'build')));
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

	function formatDate(value: string): string {
		return formatUtcInputValue(new Date(value)).replace('T', ' ');
	}

	function openRow(event: MouseEvent, entry: StrategyLibraryEntry): void {
		if ((event.target as HTMLElement).closest('a, button, input, select, label')) return;
		void goto(resolve(workspaceHref(entry.strategy_id, 'build')));
	}

	onMount(() => void loadLibrary());
</script>

<svelte:head><title>Strategies · ThyTrader</title></svelte:head>

<main>
	<div class="library-head">
		<div>
			<h1>Strategies</h1>
			<p class="lede">
				Every strategy takes one path: build, test, run on paper, run live, then review why each
				trade happened. Open a strategy to work on it.
			</p>
		</div>
		<section id="library-actions" class="top-actions" aria-label="Library actions">
			<button class="btn" type="button" onclick={openImport}>Import JSON…</button>
			<label class="template-picker"
				>Template
				<select bind:value={draftTemplate} disabled={pendingAction !== null}>
					{#each STRATEGY_TEMPLATE_OPTIONS as template (template.id)}
						<option value={template.id} title={template.description}>{template.name}</option>
					{/each}
				</select></label
			>
			<label class="template-picker"
				>Market
				<input
					class="product-input"
					bind:value={draftProduct}
					spellcheck="false"
					autocomplete="off"
					placeholder="BTC-USDC"
					disabled={pendingAction !== null}
				/></label
			>
			<label class="template-picker"
				>Clock
				<select bind:value={draftTimeframe} disabled={pendingAction !== null}>
					{#each EXECUTION_TIMEFRAMES as clock (clock)}
						<option value={clock}>{clock}</option>
					{/each}
				</select></label
			>
			<button
				class="btn primary"
				type="button"
				onclick={createNew}
				disabled={pendingAction !== null}
				>{pendingAction === 'create' ? 'Creating…' : 'New strategy'}</button
			>
		</section>
	</div>
	{#if error}<div class="error-banner" role="alert">
			<div>
				<strong>Strategy library unavailable</strong>
				<p>{error}</p>
			</div>
			<button type="button" onclick={() => loadLibrary(false)}>Retry library load</button>
		</div>{/if}
	{#if deleteResults}
		<section
			class="card results"
			role="status"
			aria-label="Deletion results"
			data-testid="delete-results"
		>
			<div class="results-head">
				<strong
					>{deleteResults.filter((item) => item.outcome === 'deleted').length} deleted{deleteResults.some(
						(item) => item.outcome !== 'deleted'
					)
						? ` · ${deleteResults.filter((item) => item.outcome !== 'deleted').length} not deleted`
						: ''}</strong
				>
				<button class="btn ghost small" type="button" onclick={() => (deleteResults = null)}
					>Dismiss</button
				>
			</div>
			<ul>
				{#each deleteResults as item (item.strategy_id)}
					<li data-outcome={item.outcome}>
						<span class="result-name">{item.name ?? nameOf(item.strategy_id)}</span>
						<span class:neg={item.outcome !== 'deleted'}>{bulkOutcomeText(item)}</span>
					</li>
				{/each}
			</ul>
		</section>
	{/if}
	{#if selected.length > 0}
		<div class="bulk-bar" role="region" aria-label="Selected strategies" data-testid="bulk-bar">
			<span>{selected.length} selected</span>
			<button class="btn ghost small" type="button" onclick={() => (selected = [])}
				>Clear selection</button
			>
			<button
				class="btn danger"
				type="button"
				disabled={pendingAction !== null || deleting}
				onclick={() =>
					void askDelete(entries.filter((entry) => selected.includes(entry.strategy_id)))}
				>Delete {selected.length} strateg{selected.length === 1 ? 'y' : 'ies'}…</button
			>
		</div>
	{/if}
	{#if tagFilter !== null}
		<div class="tag-filter" role="status" data-testid="library-tag-filter">
			<span>Showing strategies tagged</span>
			<button
				class="tag-chip active"
				type="button"
				aria-label="Clear the tag filter {tagFilter}"
				onclick={() => filterByTag(null)}>{tagFilter} ✕</button
			>
		</div>
	{/if}
	<section class="card library-card" aria-label="Strategy library">
		{#if loading}
			<div class="loading-region" aria-busy="true"><div class="skeleton wide"></div></div>
		{:else if error && entries.length === 0}
			<div class="library-empty"><p>Could not load strategies. Retry the library load.</p></div>
		{:else if entries.length === 0 && tagFilter !== null}
			<div class="library-empty">
				<p>No strategies are tagged {tagFilter}.</p>
			</div>
		{:else if entries.length === 0}
			<div class="library-empty">
				<p>No strategies yet.</p>
				<p class="empty-hint">
					Use <strong>New strategy</strong> above to create a conservative reference strategy, or import
					a strategy definition you exported elsewhere.
				</p>
			</div>
		{:else}
			<div class="table-scroll">
				<table aria-label="Strategies">
					<thead>
						<tr>
							<th scope="col" class="check-col">
								<input
									type="checkbox"
									checked={allSelected}
									indeterminate={someSelected}
									onchange={toggleAll}
									aria-label="Select all strategies on this page"
									data-testid="select-all"
								/>
							</th>
							<th scope="col">Strategy</th>
							<th scope="col">Market</th>
							<th scope="col">Progress</th>
							<th scope="col" class="num">Latest backtest</th>
							<th scope="col">Updated</th>
							<th scope="col"><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each entries as entry (entry.strategy_id)}
							{@const steps = libraryPipeline(entry)}
							<tr
								data-strategy-id={entry.strategy_id}
								class:selected={selected.includes(entry.strategy_id)}
								onclick={(event) => openRow(event, entry)}
							>
								<td class="check-col">
									<input
										type="checkbox"
										checked={selected.includes(entry.strategy_id)}
										onchange={() => toggleOne(entry.strategy_id)}
										aria-label="Select {entry.name}"
									/>
								</td>
								<td>
									<a class="strategy-name" href={resolve(workspaceHref(entry.strategy_id, 'build'))}
										>{entry.name}</a
									>
									<span class="fingerprint mono"
										>{entry.current_fingerprint
											? shortStrategyFingerprint(entry.current_fingerprint)
											: 'definition has problems'}</span
									>
									{#if (entry.tags ?? []).length > 0}
										<span class="row-tags">
											{#each entry.tags ?? [] as tag (tag)}
												<button
													class="tag-chip"
													class:active={tag === tagFilter}
													type="button"
													data-testid="library-tag-chip"
													title="Show only strategies tagged {tag}"
													onclick={() => filterByTag(tag)}>{tag}</button
												>
											{/each}
										</span>
									{/if}
								</td>
								<td
									>{entry.product_id ? marketLabel(entry.product_id) : '—'}
									{#if entry.timeframe}<span class="faint">· {entry.timeframe}</span>{/if}</td
								>
								<td>
									<span class="sr-only">{pipelineSummary(steps)}</span>
									<span class="pipe" aria-hidden="true" data-testid="library-pipeline">
										{#each steps as step (step.stage)}
											<span class="step {step.state}" title="{step.stage}: {step.detail}"
												>{step.stage}</span
											>
										{/each}
									</span>
								</td>
								<td class="num">
									{#if entry.backtest}
										<a
											class:pos={compareDecimalStrings(
												entry.backtest.summary.total_return_fraction,
												'0'
											) > 0}
											class:neg={compareDecimalStrings(
												entry.backtest.summary.total_return_fraction,
												'0'
											) < 0}
											href={resolve(
												workspaceHref(entry.strategy_id, 'test', {
													result: entry.backtest.result_fingerprint
												})
											)}
											aria-label="Latest backtest {formatPercent(
												entry.backtest.summary.total_return_fraction
											)}, {entry.backtest.summary.trade_count} trades"
											>{formatPercent(entry.backtest.summary.total_return_fraction)}</a
										>
										<span class="faint small">{entry.backtest.summary.trade_count} trades</span>
									{:else}
										<span class="faint">—</span>
									{/if}
								</td>
								<td class="faint">{formatDate(entry.updated_at)}</td>
								<td class="row-actions">
									<button
										class="btn ghost small"
										type="button"
										disabled={pendingAction !== null}
										onclick={() => void clone(entry)}
										aria-label="Clone {entry.name}"
										>{pendingAction === `clone:${entry.strategy_id}` ? 'Cloning…' : 'Clone'}</button
									>
									<button
										class="btn ghost small"
										type="button"
										disabled={pendingAction !== null || deleting}
										onclick={() => void askDelete([entry])}
										aria-label="Delete {entry.name}…">Delete…</button
									>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>
	<div class="library-pager" aria-label="Strategy pagination">
		<label
			>Rows per page
			<select data-testid="strategy-page-size" value={pageSize} onchange={changePageSize}>
				{#each [10, 25, 50, 100] as size (size)}<option value={size}>{size}</option>{/each}
			</select>
		</label>
		<span data-testid="strategy-page-range">Page {pageIndex + 1}</span>
		<button
			class="btn"
			type="button"
			onclick={previousPage}
			disabled={loading || pageIndex === 0}
			aria-label="Previous strategy page">Previous</button
		>
		<button
			class="btn"
			type="button"
			onclick={nextPage}
			disabled={loading || nextCursor === null}
			aria-label="Next strategy page">Next</button
		>
	</div>
</main>

<ConfirmDialog
	open={showImport}
	title="Import strategy JSON"
	confirmLabel="Import strategy"
	pendingLabel="Importing…"
	pending={pendingAction === 'import'}
	confirmDisabled={importText.trim().length === 0}
	confirmDisabledReason="Paste a strategy definition first."
	error={importHint}
	testId="import-dialog"
	oncancel={closeImport}
	onconfirm={() => void runImport()}
>
	<p>
		Paste one strategy definition. It becomes a new strategy with its own identity; work in progress
		with problems is saved too and shows its problems in Build.
	</p>
	<textarea
		bind:value={importText}
		rows={12}
		spellcheck="false"
		aria-label="Strategy definition JSON"></textarea>
</ConfirmDialog>

<ConfirmDialog
	open={deleteTargets.length > 0}
	title={deleteTargets.length === 1
		? `Delete ${deleteTargets[0]!.name}?`
		: `Delete ${deleteTargets.length} strategies?`}
	tone="danger"
	confirmLabel={deletable.length === 1
		? 'Delete 1 strategy'
		: `Delete ${deletable.length} strategies`}
	pendingLabel="Deleting…"
	pending={deleting}
	confirmDisabled={deletePreviewLoading || deletable.length === 0}
	confirmDisabledReason={deletePreviewLoading
		? 'Checking what would be deleted…'
		: deletePreview !== null && deletable.length === 0
			? 'Nothing selected can be deleted right now.'
			: null}
	error={deleteError}
	testId="delete-dialog"
	oncancel={closeDelete}
	onconfirm={() => void confirmDelete()}
>
	<p>
		Deleting is permanent. Each strategy and its backtests, studies, research jobs, rules snapshots,
		and paper bots (with their orders and trade reasons) are removed.
	</p>
	<p class="kept">
		Live history is kept: stopped live bots keep their orders, fills, positions, trade reasons, and
		the rules they ran, and show as “(deleted strategy)”.
	</p>
	{#if deletePreviewLoading}
		<p class="faint" aria-busy="true">Checking what would be deleted…</p>
	{:else if deletePreview}
		<ul class="preview" aria-label="What will be deleted" data-testid="delete-preview">
			{#each deletePreview as item (item.strategy_id)}
				<li data-outcome={item.outcome}>
					<strong>{item.name ?? nameOf(item.strategy_id)}</strong>
					<span class:neg={item.outcome !== 'would_delete'}>{bulkOutcomeText(item)}</span>
					{#if item.outcome === 'would_delete' && item.counts}
						<span class="faint">{deletionCountsText(item.counts).join(' · ')}</span>
					{/if}
					{#if item.outcome === 'blocked' && item.deployment_ids.length > 0}
						<span class="faint"
							>Running or paused bots can't be deleted from under you: stop them (managed stop or
							flatten) first.
							{#each item.deployment_ids as deploymentId (deploymentId)}
								<a href={resolve(`/deployments/${encodeURIComponent(deploymentId)}`)}>Open bot</a>
							{/each}</span
						>
					{/if}
				</li>
			{/each}
		</ul>
		{#if blockedCount > 0 && deletable.length > 0}
			<p class="faint">
				{blockedCount} of {deletePreview.length} will be skipped; the rest are deleted.
			</p>
		{/if}
	{/if}
</ConfirmDialog>

<style>
	.library-head {
		display: flex;
		align-items: flex-end;
		flex-wrap: wrap;
		gap: 12px;
		margin-bottom: var(--space-5);
	}
	.library-head h1 {
		margin: 0;
	}
	.top-actions {
		display: flex;
		align-items: flex-end;
		flex-wrap: wrap;
		gap: 8px;
		margin-left: auto;
	}
	.template-picker {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.template-picker select,
	.library-pager select {
		min-height: 34px;
		padding: 0 8px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
	}
	.library-card {
		overflow: hidden;
		margin-bottom: var(--space-4);
	}
	.loading-region {
		padding: 24px;
	}
	.table-scroll {
		overflow-x: auto;
	}
	th,
	td {
		text-align: left;
		vertical-align: middle;
		white-space: nowrap;
	}
	th.num,
	td.num {
		text-align: right;
	}
	tbody tr {
		cursor: pointer;
	}
	tbody tr:hover td {
		background: var(--hover);
	}
	.strategy-name {
		display: block;
		color: var(--text);
		font-weight: 500;
		text-decoration: none;
	}
	.strategy-name:hover {
		text-decoration: underline;
	}
	.fingerprint {
		display: block;
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.row-tags {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 4px;
	}
	.tag-chip {
		border: 1px solid var(--line-2);
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--muted);
		font-size: var(--fs-xs);
		padding: 1px 8px;
		cursor: pointer;
	}
	.tag-chip:hover,
	.tag-chip.active {
		border-color: var(--accent);
		color: var(--text);
		background: var(--accent-soft);
	}
	.tag-filter {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: var(--space-3);
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		display: block;
		font-size: var(--fs-xs);
	}
	.pipe {
		display: flex;
		gap: 4px;
	}
	.step {
		padding: 2px 7px;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--surface-2);
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.step.done {
		color: var(--text);
	}
	.step.active {
		border-style: dashed;
		border-color: var(--line-strong);
		color: var(--text);
	}
	.step.paper {
		border-color: transparent;
		background: var(--accent-soft);
		color: var(--accent);
	}
	.step.live {
		border-color: transparent;
		background: var(--live-soft);
		color: var(--live);
		font-weight: 600;
	}
	td a.pos {
		color: var(--pos);
	}
	td a.neg {
		color: var(--neg);
	}
	.row-actions {
		text-align: right;
	}
	.row-actions .btn + .btn {
		margin-left: 4px;
	}
	.btn.small {
		min-height: 28px;
		padding: 0 8px;
		font-size: var(--fs-sm);
	}
	.library-empty {
		padding: 42px 24px;
		text-align: center;
	}
	.library-empty p {
		margin: 0 0 8px;
	}
	.empty-hint {
		color: var(--muted);
	}
	.library-pager {
		display: flex;
		justify-content: flex-end;
		align-items: center;
		flex-wrap: wrap;
		gap: 12px;
		color: var(--muted);
	}
	.library-pager select {
		margin-left: 8px;
	}
	textarea {
		width: 100%;
		padding: 12px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
		resize: vertical;
	}
	.check-col {
		width: 36px;
	}
	tbody tr.selected td {
		background: var(--accent-soft);
	}
	.bulk-bar {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-bottom: var(--space-3);
		padding: 10px 14px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.bulk-bar span {
		margin-right: auto;
	}
	.results {
		margin-bottom: var(--space-3);
		padding: 12px 16px;
	}
	.results-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	.results ul,
	.preview {
		display: grid;
		gap: 6px;
		margin: 8px 0 0;
		padding: 0;
		list-style: none;
	}
	.results li,
	.preview li {
		display: grid;
		gap: 2px;
	}
	.result-name {
		font-weight: 500;
	}
	.neg {
		color: var(--neg);
	}
	.kept {
		color: var(--muted);
	}
	.product-input {
		width: 9rem;
		text-transform: uppercase;
	}
</style>
