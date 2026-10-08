<script lang="ts">
	/**
	 * Portfolio workspace (ADR 0088, ADR 0091): the page header with "New
	 * portfolio…", the portfolio switcher on its own row, the selected portfolio's
	 * card with its deployment state and Start / Pause / Resume / Stop, and the
	 * Sleeves · Portfolio backtest · Manager · Limits tabs.
	 *
	 * A portfolio is paper or live, never mixed. Starting runs one bot per sleeve
	 * (weight × capital); live start and live resume need the real-orders
	 * checkbox. The selected portfolio and tab live in the URL (`?portfolio=&tab=`).
	 *
	 * The switcher, the selected portfolio's card, and the start problems render
	 * as `PortfolioSwitcher`, `PortfolioCard`, and `StartProblems`; this component
	 * owns loading, polling, the URL, and every action.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { onDestroy, onMount } from 'svelte';
	import PageHead from '$lib/PageHead.svelte';
	import type { Deployment } from '$lib/deployments';
	import {
		errorText,
		fetchFillComparisons,
		fetchPortfolio,
		fetchPortfolioDeployment,
		isRevisionConflict,
		isStorageUnavailable,
		listPortfolios,
		parseTab,
		portfolioAction,
		portfolioActions,
		portfolioErrorCode,
		portfolioRunPnl,
		resetPortfolioBreaker,
		startPortfolio,
		startProblems,
		type BacktestProblem,
		type Portfolio,
		type PortfolioDeployment,
		type PortfolioDialogAction,
		type PortfolioTab
	} from '$lib/portfolios';
	import type { PortfolioFillComparisons } from '$lib/fill-comparison';
	import PortfolioActionDialog from './PortfolioActionDialog.svelte';
	import { listStrategies, type StrategyLibraryEntry } from '$lib/strategies';
	import BacktestTab from './BacktestTab.svelte';
	import LimitsTab from './LimitsTab.svelte';
	import ManagerTab from './ManagerTab.svelte';
	import NewPortfolioDialog from './NewPortfolioDialog.svelte';
	import PortfolioCard from './PortfolioCard.svelte';
	import PortfolioSwitcher from './PortfolioSwitcher.svelte';
	import SleevesTab from './SleevesTab.svelte';
	import StartProblems from './StartProblems.svelte';
	import { outcomeNotice } from './workspace';

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
	let deployment = $state<PortfolioDeployment | null>(null);
	let deploymentError = $state<string | null>(null);
	let dialogAction = $state<PortfolioDialogAction | null>(null);
	let dialogSleeve = $state<string | null>(null);
	let stopWithFlatten = $state(false);
	let acting = $state(false);
	let actionError = $state<string | null>(null);
	let actionNotice = $state<string | null>(null);
	let problems = $state<BacktestProblem[]>([]);
	let refreshTimer: ReturnType<typeof setInterval> | null = null;
	let fillsTimer: ReturnType<typeof setInterval> | null = null;
	/** Paper vs live twins of the selected portfolio's sleeves (ADR 0098). */
	let fills = $state<PortfolioFillComparisons | null>(null);

	const selected = $derived(
		portfolios?.find((item) => item.portfolio_id === selectedId) ?? portfolios?.[0] ?? null
	);
	const view = $derived(
		deployment !== null && deployment.portfolio_id === selected?.portfolio_id ? deployment : null
	);
	const actions = $derived(portfolioActions(view));
	const fillRows = $derived(
		fills !== null && fills.portfolio_id === selected?.portfolio_id ? fills : null
	);
	const pnl = $derived(portfolioRunPnl(view));

	async function loadDeployment(): Promise<void> {
		const current = selected;
		if (current === null) return;
		try {
			const next = await fetchPortfolioDeployment(current.portfolio_id);
			if (selected?.portfolio_id === current.portfolio_id) {
				deployment = next;
				deploymentError = null;
			}
		} catch (caught) {
			deploymentError = errorText(caught, 'The deployment state is unavailable.');
		}
	}

	/** The fill comparison is advisory: a failure hides the panel instead of erroring. */
	async function loadFills(): Promise<void> {
		const current = selected;
		if (current === null) return;
		try {
			const next = await fetchFillComparisons(current.portfolio_id);
			if (selected?.portfolio_id === current.portfolio_id) fills = next;
		} catch {
			if (selected?.portfolio_id === current.portfolio_id) fills = null;
		}
	}

	function openAction(action: PortfolioDialogAction, sleeveId: string | null = null): void {
		dialogAction = action;
		dialogSleeve = sleeveId;
		stopWithFlatten = false;
		actionError = null;
		problems = [];
	}

	async function confirmAction({ liveAcknowledged }: { liveAcknowledged: boolean }): Promise<void> {
		const current = selected;
		const action = dialogAction;
		if (current === null || action === null) return;
		acting = true;
		actionError = null;
		problems = [];
		try {
			if (action === 'reset') {
				deployment = await resetPortfolioBreaker(current.portfolio_id);
				actionNotice = 'Breaker reset. Sleeves stay paused until you resume them.';
			} else {
				const result =
					action === 'start'
						? await startPortfolio(current.portfolio_id, {
								revision: current.revision,
								liveAcknowledged,
								sleeveId: dialogSleeve
							})
						: await portfolioAction(current.portfolio_id, action, {
								sleeveId: dialogSleeve,
								flatten: stopWithFlatten,
								liveAcknowledged
							});
				deployment = result.deployment;
				actionNotice = outcomeNotice(result);
			}
			dialogAction = null;
			await reloadSelected();
		} catch (caught) {
			problems = startProblems(caught);
			if (isRevisionConflict(caught)) {
				await reloadSelected();
				actionError =
					'The portfolio changed since you reviewed it; reloaded — review and try again.';
			} else {
				actionError = errorText(caught, 'The portfolio action failed; nothing was changed.');
			}
		} finally {
			acting = false;
		}
	}

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
		actionNotice = null;
		syncQuery();
		void loadDeployment();
		void loadFills();
	}

	function chooseTab(next: PortfolioTab): void {
		tab = next;
		syncQuery();
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
		await loadDeployment();
	}

	function created(portfolio: Portfolio): void {
		newOpen = false;
		portfolios = [...(portfolios ?? []), portfolio];
		tab = 'sleeves';
		select(portfolio.portfolio_id);
	}

	onMount(() => {
		tab = parseTab(page.url.searchParams.get('tab'));
		void load().then(() => Promise.all([loadDeployment(), loadFills()]));
		void loadStrategies();
		// Breakers trip in the worker; refresh the deployment state while the page is open.
		refreshTimer = setInterval(() => void loadDeployment(), 20_000);
		fillsTimer = setInterval(() => void loadFills(), 60_000);
	});

	onDestroy(() => {
		if (refreshTimer !== null) clearInterval(refreshTimer);
		if (fillsTimer !== null) clearInterval(fillsTimer);
	});
</script>

<PageHead
	title="Portfolio"
	lede="A portfolio is a set of sleeves (one strategy each, with its own capital) under shared limits, run by you or a manager agent."
>
	<button
		type="button"
		class="btn ghost new-portfolio"
		data-testid="new-portfolio"
		disabled={unavailable || loading}
		onclick={() => (newOpen = true)}>New portfolio…</button
	>
</PageHead>

{#if portfolios !== null && portfolios.length > 0}
	<PortfolioSwitcher {portfolios} selectedId={selected?.portfolio_id} onselect={select} />
{/if}

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
	<PortfolioCard
		portfolio={selected}
		{view}
		{actions}
		{pnl}
		{actionNotice}
		{deploymentError}
		{tab}
		onaction={(action) => openAction(action)}
		onchoose={chooseTab}
	/>
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
					deployment={view}
					{strategies}
					{strategiesError}
					{inventory}
					onchanged={replace}
					onconflict={reloadSelected}
					onaction={openAction}
					fills={fillRows?.comparisons ?? []}
					fillWarnings={fillRows?.warnings ?? []}
				/>
			{:else if tab === 'backtest'}
				<BacktestTab portfolio={selected} onconflict={reloadSelected} />
			{:else if tab === 'manager'}
				<ManagerTab
					portfolio={selected}
					onchanged={replace}
					onconflict={reloadSelected}
					ondecided={reloadSelected}
				/>
			{:else}
				<LimitsTab
					portfolio={selected}
					deployment={view}
					onchanged={replace}
					onconflict={reloadSelected}
					onreset={() => openAction('reset')}
				/>
			{/if}
		{/key}
	</div>
{/if}

<NewPortfolioDialog open={newOpen} oncancel={() => (newOpen = false)} oncreated={created} />

{#if selected !== null}
	<PortfolioActionDialog
		portfolio={selected}
		deployment={view}
		action={dialogAction}
		sleeveId={dialogSleeve}
		bind:stopWithFlatten
		pending={acting}
		error={actionError}
		oncancel={() => (dialogAction = null)}
		onconfirm={(options) => void confirmAction(options)}
	/>
{/if}
{#if problems.length > 0}
	<StartProblems {problems} />
{/if}

<style>
	.new-portfolio {
		white-space: nowrap;
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
	.panel {
		margin-top: 16px;
	}
</style>
