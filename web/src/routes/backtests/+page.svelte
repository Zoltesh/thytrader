<script lang="ts">
	import { replaceState } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import BacktestDetail from '$lib/BacktestDetail.svelte';
	import BacktestPanel from '$lib/BacktestPanel.svelte';
	import {
		BACKTEST_LIST_DEFAULT_LIMIT,
		fetchBacktest,
		fetchBacktestBenchmark,
		fetchBacktests,
		parseResultFingerprintParam,
		type BacktestBenchmark,
		type BacktestDetail as BacktestDetailData,
		type BacktestList
	} from '$lib/backtests';

	let listing: BacktestList | null = $state(null);
	let listingAvailability = $state<'ready' | 'unavailable' | 'failed'>('ready');
	let listingLoading = $state(true);
	let listOffset = $state(0);
	let selected: BacktestDetailData | null = $state(null);
	let selectedFingerprint = $state<string | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);
	let benchmark = $state<BacktestBenchmark | null>(null);
	let benchmarkLoading = $state(false);
	let benchmarkError = $state<string | null>(null);
	let selectionRequest = 0;

	const inspecting = $derived(selectedFingerprint !== null);

	async function loadList(): Promise<void> {
		listingLoading = true;
		listingAvailability = 'ready';
		try {
			listing = await fetchBacktests({
				limit: BACKTEST_LIST_DEFAULT_LIMIT,
				offset: listOffset
			});
		} catch (caught) {
			listing = null;
			const message = caught instanceof Error ? caught.message : '';
			listingAvailability = message.includes('unavailable on this installation')
				? 'unavailable'
				: 'failed';
		} finally {
			listingLoading = false;
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

	function resetInspection(): void {
		selectionRequest += 1;
		selectedFingerprint = null;
		selected = null;
		detailError = null;
		detailLoading = false;
		benchmark = null;
		benchmarkError = null;
		benchmarkLoading = false;
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
		void loadSelectedDetail(fingerprint, requestId);
		void loadSelectedBenchmark(fingerprint, requestId);
	}

	function syncResultQuery(fingerprint: string | null): void {
		const next =
			fingerprint === null
				? resolve('/backtests')
				: resolve(`/backtests?result=${encodeURIComponent(fingerprint)}`);
		const current = `${page.url.pathname}${page.url.search}`;
		if (next === current) return;
		if (fingerprint === null) {
			replaceState(resolve('/backtests'), {});
			return;
		}
		replaceState(resolve(`/backtests?result=${encodeURIComponent(fingerprint)}`), {});
	}

	function selectBacktest(fingerprint: string): void {
		syncResultQuery(fingerprint);
	}

	function clearSelection(): void {
		syncResultQuery(null);
	}

	function showOlder(): void {
		if (listing === null || listing.returned !== listing.limit) return;
		listOffset = listing.offset + listing.limit;
		void loadList();
	}

	function showNewer(): void {
		if (listOffset <= 0) return;
		listOffset = Math.max(0, listOffset - BACKTEST_LIST_DEFAULT_LIMIT);
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

	onMount(() => {
		void loadList();
	});
</script>

<svelte:head><title>Backtests · ThyTrader</title></svelte:head>

<main>
	<section class="hero">
		<div>
			<p class="eyebrow">Research evidence</p>
			<h1>Backtests</h1>
			<p class="lede">Immutable historical simulations with disclosed assumptions.</p>
		</div>
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
	</section>
	{#if selectedFingerprint !== null}<BacktestDetail
			detail={selected}
			{benchmark}
			{benchmarkLoading}
			{benchmarkError}
			loading={detailLoading}
			error={detailError}
			onBack={clearSelection}
		/>{:else}<BacktestPanel
			entries={listing?.entries ?? []}
			bound={listing}
			loading={listingLoading}
			availability={listingAvailability}
			onSelect={(fingerprint) => void selectBacktest(fingerprint)}
			onOlder={showOlder}
			onNewer={showNewer}
		/>{/if}
</main>
