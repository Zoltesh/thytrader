<script lang="ts">
	/**
	 * Portfolio workspace (ADR 0088): the page header with the portfolio switcher
	 * and "New portfolio…", the selected portfolio's card, and the Sleeves ·
	 * Portfolio backtest · Manager · Limits tabs.
	 *
	 * A portfolio is paper or live, never mixed. Nothing here deploys: the
	 * "Deploy portfolio" action is disabled until portfolio deployment ships.
	 * The selected portfolio and tab live in the URL (`?portfolio=&tab=`).
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import PageHead from '$lib/PageHead.svelte';
	import type { Deployment } from '$lib/deployments';
	import {
		DEPLOY_ARRIVES_NEXT,
		PORTFOLIO_TABS,
		errorText,
		fetchPortfolio,
		isStorageUnavailable,
		listPortfolios,
		modeLabel,
		parseTab,
		portfolioErrorCode,
		portfolioSubtitle,
		quoteText,
		weightPercent,
		type Portfolio,
		type PortfolioTab
	} from '$lib/portfolios';
	import { listStrategies, type StrategyLibraryEntry } from '$lib/strategies';
	import BacktestTab from './BacktestTab.svelte';
	import LimitsTab from './LimitsTab.svelte';
	import ManagerTab from './ManagerTab.svelte';
	import NewPortfolioDialog from './NewPortfolioDialog.svelte';
	import SleevesTab from './SleevesTab.svelte';

	let { inventory }: { inventory: Deployment[] | null } = $props();

	let portfolios = $state<Portfolio[] | null>(null);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let unavailable = $state(false);
	let selectedId = $state<string | null>(null);
	let tab = $state<PortfolioTab>('sleeves');
	let newOpen = $state(false);
	let strategies = $state<StrategyLibraryEntry[] | null>(null);
	let strategiesError = $state<string | null>(null);

	const selected = $derived(
		portfolios?.find((item) => item.portfolio_id === selectedId) ?? portfolios?.[0] ?? null
	);

	async function load(): Promise<void> {
		loading = true;
		loadError = null;
		unavailable = false;
		try {
			const rows = await listPortfolios();
			portfolios = rows;
			const wanted = page.url.searchParams.get('portfolio');
			selectedId = rows.some((item) => item.portfolio_id === wanted)
				? wanted
				: (rows[0]?.portfolio_id ?? null);
		} catch (caught) {
			portfolios = null;
			if (isStorageUnavailable(caught)) unavailable = true;
			else loadError = errorText(caught, 'Portfolios could not be loaded.');
		} finally {
			loading = false;
		}
	}

	async function loadStrategies(): Promise<void> {
		strategiesError = null;
		try {
			strategies = await listStrategies();
		} catch (caught) {
			strategies = null;
			strategiesError = errorText(caught, 'The strategy library is unavailable.');
		}
	}

	function syncQuery(): void {
		if (selected === null) return;
		const kept = [...page.url.searchParams.entries()].filter(
			([key]) => key !== 'portfolio' && key !== 'tab'
		);
		kept.push(['portfolio', selected.portfolio_id]);
		if (tab !== 'sleeves') kept.push(['tab', tab]);
		const query = new URLSearchParams(kept);
		const next = `${resolve('/deployments')}?${query.toString()}`;
		if (`${page.url.pathname}${page.url.search}` === next) return;
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- resolved route plus query
		void goto(next, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function select(portfolioId: string): void {
		selectedId = portfolioId;
		syncQuery();
	}

	function chooseTab(next: PortfolioTab): void {
		tab = next;
		syncQuery();
	}

	function onTabKey(event: KeyboardEvent, index: number): void {
		const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
		if (delta === 0) return;
		event.preventDefault();
		const target = PORTFOLIO_TABS[(index + delta + PORTFOLIO_TABS.length) % PORTFOLIO_TABS.length];
		chooseTab(target.id);
		document.getElementById(`portfolio-tab-${target.id}`)?.focus();
	}

	function replace(updated: Portfolio): void {
		if (portfolios === null) return;
		portfolios = portfolios.map((item) =>
			item.portfolio_id === updated.portfolio_id ? updated : item
		);
	}

	/** After a 409: reload the selected portfolio (or the list when it is gone). */
	async function reloadSelected(): Promise<void> {
		if (selected === null) return;
		try {
			replace(await fetchPortfolio(selected.portfolio_id));
		} catch (caught) {
			if (portfolioErrorCode(caught) === 'portfolio_not_found') await load();
		}
	}

	function created(portfolio: Portfolio): void {
		newOpen = false;
		portfolios = [...(portfolios ?? []), portfolio];
		tab = 'sleeves';
		select(portfolio.portfolio_id);
	}

	onMount(() => {
		tab = parseTab(page.url.searchParams.get('tab'));
		void load();
		void loadStrategies();
	});
</script>

<PageHead
	title="Portfolio"
	lede="A portfolio is a set of sleeves (one strategy each, with its own capital) under shared limits, run by you or a manager agent."
>
	{#if portfolios !== null && portfolios.length > 0}
		<div class="switcher" role="group" aria-label="Choose a portfolio">
			{#each portfolios as item (item.portfolio_id)}
				<button
					type="button"
					class="btn"
					class:on={item.portfolio_id === selected?.portfolio_id}
					aria-pressed={item.portfolio_id === selected?.portfolio_id}
					data-testid="portfolio-switch"
					onclick={() => select(item.portfolio_id)}
				>
					<span class="chip" class:live={item.mode === 'live'} class:paper={item.mode === 'paper'}
						>{modeLabel(item.mode)}</span
					>
					{item.name}
				</button>
			{/each}
		</div>
	{/if}
	<button
		type="button"
		class="btn ghost"
		disabled={unavailable || loading}
		onclick={() => (newOpen = true)}>New portfolio…</button
	>
</PageHead>

{#if loading}
	<section class="loading-card" aria-label="Loading portfolios">
		<div class="skeleton wide"></div>
		<div class="skeleton"></div>
	</section>
{:else if unavailable}
	<section class="card notice" data-testid="portfolio-unavailable" role="status">
		<strong>Portfolios are unavailable right now.</strong>
		<p>
			Portfolio storage needs the database, and it did not answer. Your bots below are not affected.
		</p>
		<button type="button" class="btn" onclick={() => void load()}>Retry portfolios</button>
	</section>
{:else if loadError !== null}
	<div class="error-banner" role="alert">
		<div>
			<strong>Couldn't load portfolios</strong>
			<p>{loadError}</p>
		</div>
		<button type="button" onclick={() => void load()}>Try again</button>
	</div>
{:else if selected === null}
	<section class="empty-state" data-testid="portfolio-empty">
		<h2>No portfolios yet</h2>
		<p>
			Group strategies into sleeves with their own capital weights, set shared limits, and backtest
			them together. A portfolio is paper or live, never both.
		</p>
		<button type="button" class="btn primary" onclick={() => (newOpen = true)}
			>New portfolio…</button
		>
	</section>
{:else}
	<section
		class="card idbar"
		class:live-card={selected.mode === 'live'}
		aria-label="Selected portfolio"
		data-testid="portfolio-card"
	>
		<div class="identity">
			<div class="title-row">
				<span class="pf-name" data-testid="portfolio-name">{selected.name}</span>
				<span
					class="chip"
					class:live={selected.mode === 'live'}
					class:paper={selected.mode === 'paper'}>{modeLabel(selected.mode)}</span
				>
			</div>
			<div class="muted">{portfolioSubtitle(selected)}</div>
		</div>
		<div class="metrics">
			<div class="metric">
				<div class="l">Capital</div>
				<div class="v">{quoteText(selected.capital_quote, selected.quote_currency)}</div>
			</div>
			<div class="metric">
				<div class="l">Allocated</div>
				<div class="v">{weightPercent(selected.allocation.allocated_fraction)}</div>
			</div>
			<div class="metric">
				<div class="l">Cash reserve</div>
				<div class="v">
					{quoteText(selected.allocation.cash_reserve_quote, selected.quote_currency)}
				</div>
			</div>
			<div class="metric">
				<div class="l">Sleeves</div>
				<div class="v">{selected.sleeves.length}</div>
			</div>
			<div class="deploy">
				<button type="button" class="btn" disabled aria-describedby="deploy-note"
					>Deploy portfolio</button
				>
				<span id="deploy-note" class="faint small">{DEPLOY_ARRIVES_NEXT}</span>
			</div>
		</div>
		<div class="tabs" role="tablist" aria-label="Portfolio views">
			{#each PORTFOLIO_TABS as item, index (item.id)}
				<button
					type="button"
					role="tab"
					id="portfolio-tab-{item.id}"
					class="tab"
					class:on={tab === item.id}
					aria-selected={tab === item.id}
					aria-controls="portfolio-panel"
					tabindex={tab === item.id ? 0 : -1}
					onclick={() => chooseTab(item.id)}
					onkeydown={(event) => onTabKey(event, index)}
				>
					{item.label}
					{#if item.id === 'sleeves'}<span class="n">{selected.sleeves.length}</span>{/if}
					{#if item.id === 'manager'}<span class="chip small-chip">Settings only</span>{/if}
				</button>
			{/each}
		</div>
	</section>
	<div
		class="panel"
		id="portfolio-panel"
		role="tabpanel"
		aria-labelledby="portfolio-tab-{tab}"
		data-testid="portfolio-panel-{tab}"
	>
		{#key selected.portfolio_id}
			{#if tab === 'sleeves'}
				<SleevesTab
					portfolio={selected}
					{strategies}
					{strategiesError}
					{inventory}
					onchanged={replace}
					onconflict={reloadSelected}
				/>
			{:else if tab === 'backtest'}
				<BacktestTab portfolio={selected} onconflict={reloadSelected} />
			{:else if tab === 'manager'}
				<ManagerTab portfolio={selected} onchanged={replace} onconflict={reloadSelected} />
			{:else}
				<LimitsTab portfolio={selected} onchanged={replace} onconflict={reloadSelected} />
			{/if}
		{/key}
	</div>
{/if}

<NewPortfolioDialog open={newOpen} oncancel={() => (newOpen = false)} oncreated={created} />

<style>
	.switcher {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.switcher .btn {
		gap: 8px;
	}
	.notice {
		display: grid;
		justify-items: start;
		gap: 8px;
		padding: 16px 18px;
	}
	.notice p {
		margin: 0;
		color: var(--muted);
	}
	.idbar {
		padding: 16px 18px 0;
	}
	.live-card {
		border-color: var(--live-line);
	}
	.identity {
		min-width: 0;
	}
	.title-row {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.pf-name {
		font-size: var(--fs-lg);
		font-weight: 600;
	}
	.metrics {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 16px 28px;
		margin-top: 14px;
	}
	.metric .l {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.metric .v {
		margin-top: 2px;
		font-size: var(--fs-xl);
		font-weight: 600;
		letter-spacing: -0.01em;
	}
	.deploy {
		display: grid;
		justify-items: end;
		gap: 4px;
		margin-left: auto;
	}
	.tabs {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 14px;
		border-top: 1px solid var(--line);
	}
	.tab {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 12px 12px 10px;
		border: 0;
		border-bottom: 2px solid transparent;
		background: transparent;
		color: var(--muted);
		font: inherit;
		font-weight: 500;
		cursor: pointer;
	}
	.tab:hover {
		color: var(--text);
	}
	.tab.on {
		border-bottom-color: var(--accent);
		color: var(--text);
	}
	.n {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.small-chip {
		height: 18px;
		font-size: var(--fs-xs);
	}
	.panel {
		margin-top: 16px;
	}
	.muted {
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
	@media (max-width: 720px) {
		.deploy {
			justify-items: start;
			margin-left: 0;
		}
	}
</style>
