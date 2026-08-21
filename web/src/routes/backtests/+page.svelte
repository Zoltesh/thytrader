<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import BacktestDetail from '$lib/BacktestDetail.svelte';
	import BacktestPanel from '$lib/BacktestPanel.svelte';
	import {
		fetchBacktest,
		fetchBacktestBenchmark,
		fetchBacktests,
		type BacktestBenchmark,
		type BacktestDetail as BacktestDetailData,
		type BacktestList
	} from '$lib/backtests';

	let listing: BacktestList | null = $state(null);
	let listingAvailability = $state<'ready' | 'unavailable' | 'failed'>('ready');
	let listingLoading = $state(true);
	let selected: BacktestDetailData | null = $state(null);
	let selectedFingerprint = $state<string | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);
	let benchmark = $state<BacktestBenchmark | null>(null);
	let benchmarkLoading = $state(false);
	let benchmarkError = $state<string | null>(null);
	let selectionRequest = 0;

	async function loadList(): Promise<void> {
		listingLoading = true;
		listingAvailability = 'ready';
		try {
			listing = await fetchBacktests();
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

	function selectBacktest(fingerprint: string): void {
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

	function clearSelection(): void {
		selectionRequest += 1;
		selectedFingerprint = null;
		selected = null;
		detailError = null;
		detailLoading = false;
		benchmark = null;
		benchmarkError = null;
		benchmarkLoading = false;
	}

	onMount(() => {
		void loadList();
		const fingerprint = new URLSearchParams(window.location.search).get('result');
		if (fingerprint?.match(/^sha256:[0-9a-f]{64}$/)) selectBacktest(fingerprint);
	});
</script>

<svelte:head><title>Backtests · ThyTrader</title></svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home"
			><span class="brand-mark">T</span><span>ThyTrader</span></a
		>
		<nav aria-label="Primary navigation">
			<a href={resolve('/')}>Portfolio</a>
			<a href={resolve('/strategies')}>Strategies</a>
			<a class="active" href={resolve('/backtests')}>Backtests</a>
			<a href={resolve('/audit')}>Audit</a>
		</nav>
		<div class="local-pill"><span></span> Local workstation</div>
	</header>
	<main>
		<section class="hero">
			<div>
				<p class="eyebrow">Research evidence</p>
				<h1>Backtests</h1>
				<p class="lede">Immutable historical simulations with disclosed assumptions.</p>
			</div>
			<button class="refresh" type="button" onclick={loadList} disabled={listingLoading}
				><span class:spinning={listingLoading}>↻</span>{listingLoading
					? 'Refreshing…'
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
				loading={listingLoading}
				availability={listingAvailability}
				onSelect={(fingerprint) => void selectBacktest(fingerprint)}
			/>{/if}
	</main>
</div>
