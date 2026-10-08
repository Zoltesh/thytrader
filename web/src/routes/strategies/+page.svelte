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
	 *
	 * The library opens on "Mine" (ADR 0098): agent research strategies
	 * (tagged `claude-research` or `research-*`) sit under "Research" so a burst
	 * of research never buries the operator's own work. The choice is kept per
	 * viewer. Row tag chips filter by that tag within the current view.
	 *
	 * The page owns the library state and actions; its sections render from
	 * `$lib/strategies-page/` and pure helpers live in `strategies-page/library.ts`.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import {
		bulkDeleteStrategies,
		cloneStrategy,
		createStrategy,
		deleteStrategy,
		fetchStrategyPage,
		importStrategy,
		readStoredOrigin,
		rememberOrigin,
		STRATEGY_ORIGIN_OPTIONS,
		type BulkDeleteItem,
		type StrategyLibraryEntry,
		type StrategyOrigin
	} from '$lib/strategies';
	import BulkBar from '$lib/strategies-page/BulkBar.svelte';
	import DeleteResults from '$lib/strategies-page/DeleteResults.svelte';
	import DeleteStrategiesDialog from '$lib/strategies-page/DeleteStrategiesDialog.svelte';
	import ImportStrategyDialog from '$lib/strategies-page/ImportStrategyDialog.svelte';
	import LibraryEmpty from '$lib/strategies-page/LibraryEmpty.svelte';
	import LibraryFilters from '$lib/strategies-page/LibraryFilters.svelte';
	import LibraryHeader from '$lib/strategies-page/LibraryHeader.svelte';
	import LibraryPager from '$lib/strategies-page/LibraryPager.svelte';
	import LibraryTable from '$lib/strategies-page/LibraryTable.svelte';
	import {
		deletedItem,
		failedDeleteItem,
		readStoredProduct,
		rememberProduct
	} from '$lib/strategies-page/library';
	import { workspaceHref } from '$lib/strategy-workspace';

	let entries = $state<StrategyLibraryEntry[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let pageSize = $state<10 | 25 | 50 | 100>(10);
	/** Show only strategies whose metadata.tags include this tag (ADR 0094). */
	let tagFilter = $state<string | null>(null);
	/** Mine (operator), Research, or All; remembered per viewer (ADR 0098). */
	let origin = $state<StrategyOrigin>(readStoredOrigin());
	/** Matches in the current view, from the server's `total`. */
	let total = $state<number | null>(null);
	let originCounts = $state<Record<StrategyOrigin, number> | null>(null);
	const originOptions = $derived(
		STRATEGY_ORIGIN_OPTIONS.map((option) => ({
			...option,
			count: originCounts?.[option.id]
		}))
	);
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
	let draftProduct = $state(readStoredProduct());

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
			const page = await fetchStrategyPage(pageSize, pageCursors[pageIndex], tagFilter, origin);
			if (requestId !== libraryRequestId) return;
			if (page.entries.length === 0 && pageIndex > 0) {
				pageIndex -= 1;
				void loadLibrary(false);
				return;
			}
			entries = page.entries;
			nextCursor = page.nextCursor;
			total = page.total;
			originCounts = page.originCounts;
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

	function chooseOrigin(next: StrategyOrigin): void {
		if (next === origin) return;
		origin = next;
		rememberOrigin(next);
		selected = [];
		void loadLibrary();
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
			return deletedItem(strategyId, await deleteStrategy(strategyId));
		} catch (caught) {
			return failedDeleteItem(strategyId, nameOf(strategyId), caught);
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

	function openRow(event: MouseEvent, entry: StrategyLibraryEntry): void {
		if ((event.target as HTMLElement).closest('a, button, input, select, label')) return;
		void goto(resolve(workspaceHref(entry.strategy_id, 'build')));
	}

	onMount(() => void loadLibrary());
</script>

<svelte:head><title>Strategies · ThyTrader</title></svelte:head>

<main>
	<LibraryHeader
		bind:draftTemplate
		bind:draftProduct
		bind:draftTimeframe
		{pendingAction}
		onimport={openImport}
		oncreate={createNew}
	/>
	{#if error}<div class="error-banner" role="alert">
			<div>
				<strong>Strategy library unavailable</strong>
				<p>{error}</p>
			</div>
			<button type="button" onclick={() => loadLibrary(false)}>Retry library load</button>
		</div>{/if}
	{#if deleteResults}
		<DeleteResults results={deleteResults} {nameOf} ondismiss={() => (deleteResults = null)} />
	{/if}
	{#if selected.length > 0}
		<BulkBar
			count={selected.length}
			disabled={pendingAction !== null || deleting}
			onclear={() => (selected = [])}
			ondelete={() =>
				void askDelete(entries.filter((entry) => selected.includes(entry.strategy_id)))}
		/>
	{/if}
	<LibraryFilters
		{originOptions}
		{origin}
		onorigin={chooseOrigin}
		{tagFilter}
		onclearTag={() => filterByTag(null)}
		{total}
		{loading}
	/>
	<section class="card library-card" aria-label="Strategy library">
		{#if loading}
			<div class="loading-region" aria-busy="true"><div class="skeleton wide"></div></div>
		{:else if entries.length === 0}
			<LibraryEmpty
				failed={Boolean(error)}
				{origin}
				{tagFilter}
				onresearch={() => chooseOrigin('research')}
			/>
		{:else}
			<LibraryTable
				{entries}
				{selected}
				{allSelected}
				{someSelected}
				{tagFilter}
				{pendingAction}
				{deleting}
				ontoggleAll={toggleAll}
				ontoggleOne={toggleOne}
				onopen={openRow}
				ontag={filterByTag}
				onclone={(entry) => void clone(entry)}
				ondelete={(entry) => void askDelete([entry])}
			/>
		{/if}
	</section>
	<LibraryPager
		{pageSize}
		{pageIndex}
		{loading}
		{nextCursor}
		onpagesize={changePageSize}
		onprevious={previousPage}
		onnext={nextPage}
	/>
</main>

<ImportStrategyDialog
	open={showImport}
	bind:importText
	pending={pendingAction === 'import'}
	{importHint}
	oncancel={closeImport}
	onconfirm={() => void runImport()}
/>

<DeleteStrategiesDialog
	{deleteTargets}
	{deletePreview}
	{deletePreviewLoading}
	{deletable}
	{blockedCount}
	{deleting}
	{deleteError}
	{nameOf}
	oncancel={closeDelete}
	onconfirm={() => void confirmDelete()}
/>

<style>
	.library-card {
		overflow: hidden;
		margin-bottom: var(--space-4);
	}
	.loading-region {
		padding: 24px;
	}
</style>
