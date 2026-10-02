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
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { onDestroy, onMount } from 'svelte';
	import PageHead from '$lib/PageHead.svelte';
	import type { Deployment } from '$lib/deployments';
	import {
		PORTFOLIO_TABS,
		deploymentStateLabel,
		errorText,
		fetchPortfolio,
		fetchPortfolioDeployment,
		isRevisionConflict,
		isStorageUnavailable,
		listPortfolios,
		modeLabel,
		parseTab,
		portfolioAction,
		portfolioActions,
		portfolioErrorCode,
		portfolioSubtitle,
		quoteText,
		resetPortfolioBreaker,
		signedQuote,
		startPortfolio,
		startProblems,
		weightPercent,
		type BacktestProblem,
		type Portfolio,
		type PortfolioActionResponse,
		type PortfolioDeployment,
		type PortfolioDialogAction,
		type PortfolioTab
	} from '$lib/portfolios';
	import { subtractDecimalStrings } from '$lib/portfolio';
	import PortfolioActionDialog from './PortfolioActionDialog.svelte';
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

	const selected = $derived(
		portfolios?.find((item) => item.portfolio_id === selectedId) ?? portfolios?.[0] ?? null
	);
	const view = $derived(
		deployment !== null && deployment.portfolio_id === selected?.portfolio_id ? deployment : null
	);
	const actions = $derived(portfolioActions(view));
	const pnl = $derived(
		view === null ? null : subtractDecimalStrings(view.breaker.equity, view.capital_quote)
	);

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

	function openAction(action: PortfolioDialogAction, sleeveId: string | null = null): void {
		dialogAction = action;
		dialogSleeve = sleeveId;
		stopWithFlatten = false;
		actionError = null;
		problems = [];
	}

	function outcomeNotice(result: PortfolioActionResponse): string {
		const changed = result.outcomes.filter(
			(item) => item.outcome !== 'unchanged' && item.outcome !== 'failed'
		);
		const failed = result.outcomes.filter((item) => item.outcome === 'failed');
		const verb = { start: 'Started', pause: 'Paused', resume: 'Resumed', stop: 'Stopped' }[
			result.action
		];
		const count = `${changed.length} sleeve${changed.length === 1 ? '' : 's'}`;
		const failures =
			failed.length === 0
				? ''
				: ` ${failed.length} could not: ${failed.map((item) => `${item.strategy_name} (${item.message ?? 'no reason'})`).join('; ')}.`;
		return `${verb} ${count}.${failures}`;
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
		void load().then(loadDeployment);
		void loadStrategies();
		// Breakers trip in the worker; refresh the deployment state while the page is open.
		refreshTimer = setInterval(() => void loadDeployment(), 20_000);
	});

	onDestroy(() => {
		if (refreshTimer !== null) clearInterval(refreshTimer);
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
	<div
		class="switcher"
		role="group"
		aria-label="Choose a portfolio"
		data-testid="portfolio-switcher"
	>
		{#each portfolios as item (item.portfolio_id)}
			<button
				type="button"
				class="btn switch"
				class:on={item.portfolio_id === selected?.portfolio_id}
				aria-pressed={item.portfolio_id === selected?.portfolio_id}
				title={item.name}
				data-testid="portfolio-switch"
				onclick={() => select(item.portfolio_id)}
			>
				<span class="chip" class:live={item.mode === 'live'} class:paper={item.mode === 'paper'}
					>{modeLabel(item.mode)}</span
				>
				<span class="switch-name">{item.name}</span>
			</button>
		{/each}
	</div>
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
	<section
		class="card idbar"
		class:live-card={selected.mode === 'live'}
		aria-label="Selected portfolio"
		data-testid="portfolio-card"
	>
		<div class="identity">
			<div class="title-row">
				<span class="pf-name" data-testid="portfolio-name" title={selected.name}
					>{selected.name}</span
				>
				<span
					class="chip"
					class:live={selected.mode === 'live'}
					class:paper={selected.mode === 'paper'}>{modeLabel(selected.mode)}</span
				>
				<span class="chip" data-testid="portfolio-state" class:running={view?.state === 'running'}
					>{deploymentStateLabel(view?.state ?? selected.deployment_state ?? 'not_deployed')}</span
				>
				{#if view?.breaker.latched}
					<span class="chip breaker" data-testid="portfolio-breaker-chip">Breaker latched</span>
				{/if}
			</div>
			<div class="muted">{portfolioSubtitle(selected, view?.state ?? null)}</div>
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
			{#if view !== null && view.state !== 'not_deployed'}
				<div class="metric" data-testid="portfolio-equity">
					<div class="l">Equity (this run)</div>
					<div class="v">
						{quoteText(view.breaker.equity, selected.quote_currency)}
						{#if pnl !== null}<span class="delta small" data-testid="portfolio-pnl"
								>{signedQuote(pnl, selected.quote_currency)}</span
							>{/if}
					</div>
				</div>
				<div class="metric">
					<div class="l">Exposure</div>
					<div class="v">{weightPercent(view.exposure.fraction_of_capital)}</div>
				</div>
			{/if}
			<div class="deploy" data-testid="portfolio-controls">
				<div class="deploy-buttons">
					<button
						type="button"
						class={view === null || view.state === 'not_deployed' ? 'btn primary' : 'btn'}
						data-testid="portfolio-start"
						disabled={!actions.start || !selected.deployable}
						onclick={() => openAction('start')}
						>{view?.state === 'not_deployed' || view === null
							? 'Start portfolio…'
							: 'Start stopped sleeves…'}</button
					>
					<button
						type="button"
						class="btn"
						data-testid="portfolio-pause"
						disabled={!actions.pause}
						onclick={() => openAction('pause')}>Pause all</button
					>
					<button
						type="button"
						class="btn"
						data-testid="portfolio-resume"
						disabled={!actions.resume}
						onclick={() => openAction('resume')}>Resume…</button
					>
					<button
						type="button"
						class="btn danger"
						data-testid="portfolio-stop"
						disabled={!actions.stop}
						onclick={() => openAction('stop')}>Stop…</button
					>
				</div>
				{#if !selected.deployable}
					<span class="faint small">Add sleeves and fix their issues before starting.</span>
				{:else if view?.breaker.latched}
					<span class="faint small">A breaker is latched: reset it on the Limits tab first.</span>
				{/if}
			</div>
		</div>
		{#if actionNotice}<p class="action-notice small" role="status">{actionNotice}</p>{/if}
		{#if deploymentError}<p class="problem small" role="alert">{deploymentError}</p>{/if}
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
					{#if item.id === 'manager' && (view?.pending_proposals ?? 0) > 0}<span
							class="chip small-chip waiting"
							data-testid="manager-waiting">{view?.pending_proposals} waiting</span
						>{/if}
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
					deployment={view}
					{strategies}
					{strategiesError}
					{inventory}
					onchanged={replace}
					onconflict={reloadSelected}
					onaction={openAction}
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
	<section class="card problems" role="alert" data-testid="portfolio-start-problems">
		<strong>The portfolio was not started. Nothing changed.</strong>
		<ul>
			{#each problems as problem, index (index)}
				<li>
					<b>{problem.strategy_name ?? 'Portfolio'}</b>: {problem.message}
					<span class="faint small">({problem.code})</span>
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.switcher {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin: calc(-1 * var(--space-3)) 0 var(--space-4);
		max-width: 100%;
	}
	.switch {
		gap: 8px;
		max-width: min(320px, 100%);
		min-width: 0;
	}
	.switch-name {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.new-portfolio {
		white-space: nowrap;
	}
	.chip.running {
		border-color: var(--pos);
		color: var(--pos);
	}
	.chip.breaker {
		border-color: var(--neg);
		color: var(--neg);
	}
	.waiting {
		border-color: var(--accent);
		color: var(--accent);
	}
	.deploy-buttons {
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: 8px;
	}
	.delta {
		margin-left: 6px;
		color: var(--muted);
		font-weight: 500;
	}
	.action-notice {
		margin: 10px 0 0;
		color: var(--muted);
	}
	.problem {
		margin: 10px 0 0;
		color: var(--neg);
	}
	.problems {
		display: grid;
		gap: 8px;
		margin-top: 16px;
		padding: 14px 18px;
		border-color: var(--neg);
	}
	.problems ul {
		margin: 0;
		padding-left: 18px;
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
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		min-width: 0;
	}
	.pf-name {
		min-width: 0;
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
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
