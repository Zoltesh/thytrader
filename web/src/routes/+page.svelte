<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import MarketDataPanel from '$lib/MarketDataPanel.svelte';
	import PortfolioChart from '$lib/PortfolioChart.svelte';
	import {
		formatUsd,
		permissionLabel,
		type ApiError,
		type HistoryEntry,
		type HistoryRange,
		type MarketDataPreview,
		type Portfolio,
		type PortfolioHistory
	} from '$lib/portfolio';

	let portfolio: Portfolio | null = $state(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let history: HistoryEntry[] = $state([]);
	let historyLoading = $state(true);
	let historyAvailability: 'ready' | 'unavailable' | 'failed' = $state('ready');
	let historyRange: HistoryRange = $state('24h');
	let samplingIntervalSeconds = $state(300);
	let marketDataPreview: MarketDataPreview | null = $state(null);
	let marketDataLoading = $state(true);
	let marketDataAvailability: 'ready' | 'failed' = $state('ready');

	async function loadHistory(range = historyRange): Promise<void> {
		historyLoading = true;
		historyAvailability = 'ready';
		try {
			const response = await fetch(`/api/v1/portfolio/history?range=${range}`, {
				headers: { Accept: 'application/json' }
			});
			if (response.ok) {
				const body = (await response.json()) as PortfolioHistory;
				history = body.entries;
				historyRange = body.range;
				samplingIntervalSeconds = body.sampling_interval_seconds;
			} else {
				history = [];
				historyAvailability = response.status === 503 ? 'unavailable' : 'failed';
			}
		} catch {
			history = [];
			historyAvailability = 'failed';
		} finally {
			historyLoading = false;
		}
	}

	async function loadMarketData(): Promise<void> {
		marketDataLoading = true;
		marketDataAvailability = 'ready';
		try {
			const response = await fetch('/api/v1/market-data/preview', {
				headers: { Accept: 'application/json' }
			});
			if (!response.ok) {
				marketDataPreview = null;
				marketDataAvailability = 'failed';
				return;
			}
			marketDataPreview = (await response.json()) as MarketDataPreview;
		} catch {
			marketDataPreview = null;
			marketDataAvailability = 'failed';
		} finally {
			marketDataLoading = false;
		}
	}

	async function loadPortfolio(): Promise<void> {
		loading = true;
		error = null;
		try {
			const response = await fetch('/api/v1/portfolio', {
				headers: { Accept: 'application/json' }
			});
			if (!response.ok) {
				const body = (await response.json()) as ApiError;
				throw new Error(body.detail?.message ?? 'Portfolio data is unavailable.');
			}
			portfolio = (await response.json()) as Portfolio;
			await Promise.all([loadHistory(), loadMarketData()]);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Portfolio data is unavailable.';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void loadPortfolio();
	});
</script>

<svelte:head><title>Your portfolio · ThyTrader</title></svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home">
			<span class="brand-mark">T</span>
			<span>ThyTrader</span>
		</a>
		<nav aria-label="Primary navigation">
			<a class="active" href={resolve('/')}>Portfolio</a>
			<span>Strategies</span>
			<span>Backtests</span>
		</nav>
		<div class="local-pill"><span></span> Local workstation</div>
	</header>

	<main>
		<section class="hero">
			<div>
				<p class="eyebrow">Coinbase overview</p>
				<h1>Your portfolio</h1>
				<p class="lede">Balances and estimated value from your connected Coinbase account.</p>
			</div>
			<button class="refresh" type="button" onclick={loadPortfolio} disabled={loading}>
				<span class:spinning={loading}>↻</span>
				{loading ? 'Refreshing…' : 'Refresh portfolio'}
			</button>
		</section>

		{#if error}
			<div class="error-banner" role="alert">
				<div>
					<strong>Couldn't refresh Coinbase</strong>
					<p>{error}</p>
				</div>
				<button type="button" onclick={loadPortfolio}>Try again</button>
			</div>
		{/if}

		{#if loading && !portfolio}
			<section class="loading-card" aria-label="Loading portfolio">
				<div class="skeleton wide"></div>
				<div class="skeleton"></div>
				<div class="skeleton"></div>
			</section>
		{:else if portfolio}
			{#if portfolio.demo}
				<div class="demo-banner">
					<div><span class="demo-dot"></span><strong>Demo data</strong></div>
					<p>Add Coinbase credentials to <code>.env</code> to display your live balances.</p>
				</div>
			{/if}

			<section class="summary-grid">
				<article class="value-card">
					<p>Estimated portfolio value</p>
					<strong>{formatUsd(portfolio.total_value.amount)}</strong>
					<span>USD estimate</span>
				</article>
				<article class="connection-card">
					<div class="card-heading">
						<p>Coinbase connection</p>
						<span class="status {portfolio.connection.status}">{portfolio.connection.status}</span>
					</div>
					<strong
						>{portfolio.connection.status === 'connected'
							? 'Connected'
							: 'Ready to preview'}</strong
					>
					<small>Updated {new Date(portfolio.as_of).toLocaleString()}</small>
				</article>
				<article class="permissions-card">
					<p>Detected permissions</p>
					<div class="permissions">
						{#each portfolio.connection.permissions as permission (permission)}
							<span>{permissionLabel(permission)}</span>
						{/each}
					</div>
					<small>Additional permissions do not block connection.</small>
				</article>
			</section>

			<PortfolioChart
				entries={history}
				loading={historyLoading}
				availability={historyAvailability}
				selectedRange={historyRange}
				{samplingIntervalSeconds}
				onRangeChange={(range) => void loadHistory(range)}
			/>

			<MarketDataPanel
				preview={marketDataPreview}
				loading={marketDataLoading}
				availability={marketDataAvailability}
			/>

			<section class="asset-panel">
				<div class="panel-heading">
					<div>
						<h2>Assets</h2>
						<p>{portfolio.assets.length} balances with value</p>
					</div>
					<span>Estimated in USD</span>
				</div>
				<div class="table-wrap">
					<table>
						<thead
							><tr
								><th>Asset</th><th>Available</th><th>On hold</th><th>Total</th><th>Est. value</th
								></tr
							></thead
						>
						<tbody>
							{#each portfolio.assets as asset (asset.currency)}
								<tr>
									<td
										><div class="asset-name">
											<span class="coin">{asset.currency.slice(0, 1)}</span>
											<div><strong>{asset.name}</strong><small>{asset.currency}</small></div>
										</div></td
									>
									<td>{asset.available}</td><td>{asset.hold}</td><td>{asset.total}</td>
									<td class="asset-value"
										>{asset.value ? formatUsd(asset.value.amount) : 'Unavailable'}</td
									>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#if portfolio.unvalued_assets.length}
					<p class="unvalued">No direct USD valuation: {portfolio.unvalued_assets.join(', ')}</p>
				{/if}
			</section>
		{/if}
	</main>
</div>
