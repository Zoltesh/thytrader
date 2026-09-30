<script lang="ts">
	import PageHead from '$lib/PageHead.svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import BacktestDetail from '$lib/BacktestDetail.svelte';
	import BacktestPanel from '$lib/BacktestPanel.svelte';
	import {
		BACKTEST_LIST_DEFAULT_LIMIT,
		fetchBacktest,
		fetchBacktestBenchmark,
		fetchBacktestMetrics,
		fetchBacktests,
		parseResultFingerprintParam,
		type BacktestBenchmark,
		type BacktestDetail as BacktestDetailData,
		type BacktestList,
		type BacktestPerformanceMetrics
	} from '$lib/backtests';
	import { workspaceHref } from '$lib/strategy-workspace';
	import { resolveStrategyOwner } from '$lib/workspace-data';

	let listing: BacktestList | null = $state(null);
	let listingAvailability = $state<'ready' | 'unavailable' | 'failed'>('ready');
	let listingLoading = $state(true);
	let listOffset = $state(0);
	let pageSize = $state<10 | 25 | 50 | 100>(BACKTEST_LIST_DEFAULT_LIMIT);
	let listRequest = 0;
	let selected: BacktestDetailData | null = $state(null);
	let selectedFingerprint = $state<string | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);
	let benchmark = $state<BacktestBenchmark | null>(null);
	let benchmarkLoading = $state(false);
	let benchmarkError = $state<string | null>(null);
	let metrics = $state<BacktestPerformanceMetrics | null>(null);
	let metricsLoading = $state(false);
	let metricsError = $state<string | null>(null);
	let selectionRequest = 0;
	let loadedFilter: string | null | undefined;

	const inspecting = $derived(selectedFingerprint !== null);
	const strategyFilter = $derived(page.url.searchParams.get('strategy_fingerprint'));

	async function loadList(): Promise<void> {
		const requestId = ++listRequest;
		listingLoading = true;
		listingAvailability = 'ready';
		try {
			const result = await fetchBacktests({
				limit: pageSize,
				offset: listOffset,
				...(strategyFilter !== null ? { strategy_fingerprint: strategyFilter } : {})
			});
			if (requestId === listRequest) listing = result;
		} catch (caught) {
			if (requestId !== listRequest) return;
			listing = null;
			const message = caught instanceof Error ? caught.message : '';
			listingAvailability = message.includes('unavailable on this installation')
				? 'unavailable'
				: 'failed';
		} finally {
			if (requestId === listRequest) listingLoading = false;
		}
	}

	async function loadSelectedDetail(fingerprint: string, requestId: number): Promise<void> {
		try {
			const result = await fetchBacktest(fingerprint);
			if (requestId !== selectionRequest) return;
			selected = result;
		} catch (caught) {
			if (requestId !== selectionRequest) return;
			detailError = caught instanceof Error ? caught.message : 'Backtest result is unavailable.';
		} finally {
			if (requestId === selectionRequest) detailLoading = false;
		}
	}

	async function loadSelectedBenchmark(fingerprint: string, requestId: number): Promise<void> {
		try {
			const result = await fetchBacktestBenchmark(fingerprint);
			if (requestId !== selectionRequest) return;
			benchmark = result.benchmark;
		} catch (caught) {
			if (requestId !== selectionRequest) return;
			benchmarkError =
				caught instanceof Error ? caught.message : 'Backtest benchmark is unavailable.';
		} finally {
			if (requestId === selectionRequest) benchmarkLoading = false;
		}
	}

	async function loadSelectedMetrics(fingerprint: string, requestId: number): Promise<void> {
		try {
			const result = await fetchBacktestMetrics(fingerprint);
			if (requestId !== selectionRequest) return;
			metrics = result.metrics;
		} catch (caught) {
			if (requestId !== selectionRequest) return;
			metricsError = caught instanceof Error ? caught.message : 'Backtest metrics are unavailable.';
		} finally {
			if (requestId === selectionRequest) metricsLoading = false;
		}
	}

	function resetInspection(): void {
		selectionRequest += 1;
		selectedFingerprint = null;
		selected = null;
		detailError = null;
		detailLoading = false;
		benchmark = null;
		benchmarkError = null;
		benchmarkLoading = false;
		metrics = null;
		metricsError = null;
		metricsLoading = false;
	}

	function beginInspection(fingerprint: string): void {
		if (selectedFingerprint === fingerprint) return;
		const requestId = ++selectionRequest;
		selectedFingerprint = fingerprint;
		selected = null;
		detailError = null;
		detailLoading = true;
		benchmark = null;
		benchmarkError = null;
		benchmarkLoading = true;
		metrics = null;
		metricsError = null;
		metricsLoading = true;
		void loadSelectedDetail(fingerprint, requestId);
		void loadSelectedBenchmark(fingerprint, requestId);
		void loadSelectedMetrics(fingerprint, requestId);
	}

	function syncResultQuery(fingerprint: string | null): void {
		const filter =
			strategyFilter !== null ? `strategy_fingerprint=${encodeURIComponent(strategyFilter)}` : '';
		const current = `${page.url.pathname}${page.url.search}`;
		if (fingerprint === null) {
			const next = `${resolve('/backtests')}${filter ? `?${filter}` : ''}`;
			if (current === next) return;
			// eslint-disable-next-line svelte/no-navigation-without-resolve -- resolved route plus preserved query
			void goto(next, { replaceState: true, keepFocus: true, noScroll: true });
			return;
		}
		const next = resolve(
			`/backtests?${filter ? `${filter}&` : ''}result=${encodeURIComponent(fingerprint)}`
		);
		if (current === next) return;
		void goto(next, {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	}

	function selectBacktest(fingerprint: string): void {
		beginInspection(fingerprint);
		syncResultQuery(fingerprint);
	}

	function clearSelection(): void {
		resetInspection();
		syncResultQuery(null);
	}

	function showOlder(): void {
		if (
			listingLoading ||
			listing === null ||
			listing.has_more === false ||
			listing.returned !== listing.limit
		)
			return;
		listOffset = listing.offset + listing.limit;
		void loadList();
	}

	function showNewer(): void {
		if (listingLoading || listOffset <= 0) return;
		listOffset = Math.max(0, listOffset - pageSize);
		void loadList();
	}

	function changePageSize(event: Event): void {
		pageSize = Number((event.currentTarget as HTMLSelectElement).value) as typeof pageSize;
		listOffset = 0;
		void loadList();
	}

	$effect(() => {
		const fingerprint = parseResultFingerprintParam(page.url.searchParams.get('result'));
		if (fingerprint !== null) {
			beginInspection(fingerprint);
			return;
		}
		resetInspection();
	});

	let redirecting = $state(false);

	/**
	 * Old deep links keep working: when the result's (or filter's) strategy
	 * fingerprint is a snapshot of a strategy that still exists, open that strategy's
	 * Test stage. Otherwise stay on this standalone view.
	 */
	async function redirectToOwner(): Promise<void> {
		const result = parseResultFingerprintParam(page.url.searchParams.get('result'));
		let fingerprint = strategyFilter;
		if (result !== null) {
			try {
				fingerprint = (await fetchBacktest(result)).result.strategy_fingerprint;
			} catch {
				return;
			}
		}
		if (fingerprint === null || fingerprint === '') return;
		const owner = await resolveStrategyOwner(fingerprint);
		if (owner.kind !== 'owned') return;
		redirecting = true;
		await goto(resolve(workspaceHref(owner.strategyId, 'test', { result })), {
			replaceState: true
		});
	}

	onMount(() => {
		void loadList();
		void redirectToOwner();
	});
	$effect(() => {
		const filter = strategyFilter;
		if (loadedFilter === undefined) {
			loadedFilter = filter;
			return;
		}
		if (loadedFilter === filter) return;
		loadedFilter = filter;
		listOffset = 0;
		void loadList();
	});
</script>

<svelte:head><title>Backtests · ThyTrader</title></svelte:head>

<main>
	<PageHead eyebrow="Research evidence" title="Backtests">
		{#snippet intro()}
			<p class="lede">
				Immutable historical simulations with disclosed assumptions. Running backtests and reading
				one strategy's results now happen on each strategy's Test stage.
				<a href={resolve('/strategies')}>Open a strategy</a>
			</p>
			{#if redirecting}<p class="lede" role="status">
					Opening this in its strategy workspace…
				</p>{/if}
			{#if strategyFilter !== null}
				<p class="lede">
					Filtered to published strategy version <code>{strategyFilter}</code>.
					<a href={resolve('/backtests')}>Show all backtests</a>
				</p>
			{/if}
		{/snippet}
		<button
			class="refresh"
			type="button"
			onclick={loadList}
			disabled={listingLoading}
			aria-label={inspecting
				? 'Reload published backtest list without changing this immutable result'
				: 'Refresh published backtest results'}
			><span class:spinning={listingLoading}>↻</span>{listingLoading
				? inspecting
					? 'Reloading list…'
					: 'Refreshing…'
				: inspecting
					? 'Reload list'
					: 'Refresh results'}</button
		>
	</PageHead>
	{#if selectedFingerprint !== null}<BacktestDetail
			detail={selected}
			{benchmark}
			{benchmarkLoading}
			{benchmarkError}
			{metrics}
			{metricsLoading}
			{metricsError}
			loading={detailLoading}
			error={detailError}
			onBack={clearSelection}
		/>{:else}<BacktestPanel
			entries={listing?.entries ?? []}
			bound={listing}
			loading={listingLoading}
			availability={listingAvailability}
			{pageSize}
			onPageSizeChange={changePageSize}
			onSelect={(fingerprint) => void selectBacktest(fingerprint)}
			onOlder={showOlder}
			onNewer={showNewer}
		/>{/if}
</main>
