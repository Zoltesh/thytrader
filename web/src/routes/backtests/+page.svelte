<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import BacktestDetail from '$lib/BacktestDetail.svelte';
	import BacktestPanel from '$lib/BacktestPanel.svelte';
	import {
		fetchBacktest,
		fetchBacktests,
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

	async function selectBacktest(fingerprint: string): Promise<void> {
		selectedFingerprint = fingerprint;
		selected = null;
		detailError = null;
		detailLoading = true;
		try {
			selected = await fetchBacktest(fingerprint);
		} catch (caught) {
			detailError = caught instanceof Error ? caught.message : 'Backtest result is unavailable.';
		} finally {
			detailLoading = false;
		}
	}

	function clearSelection(): void {
		selectedFingerprint = null;
		selected = null;
		detailError = null;
	}

	onMount(() => void loadList());
</script>

<svelte:head><title>Backtests · ThyTrader</title></svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home"
			><span class="brand-mark">T</span><span>ThyTrader</span></a
		>
		<nav aria-label="Primary navigation">
			<a href={resolve('/')}>Portfolio</a><span>Strategies</span><a
				class="active"
				href={resolve('/backtests')}>Backtests</a
			>
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
