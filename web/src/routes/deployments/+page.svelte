<script lang="ts">
	/**
	 * Portfolio (`/deployments`): portfolios (sleeves of strategies under shared
	 * limits, ADR 0088) on top, then "All bots": every bot on this workstation,
	 * one row per deployment, grouped Needs attention · Running · Paused · Stopped.
	 *
	 * The bot list is the bounded, offset-paged inventory. Header counts and money
	 * come from the full inventory (followed page by page) and are never
	 * invented: money is shown per mode and quote currency, else `—` with the
	 * reason. Portfolio deployment is not built yet; each bot still runs alone.
	 */
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import PreflightPanel from '$lib/PreflightPanel.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import PortfolioWorkspace from '$lib/portfolios/PortfolioWorkspace.svelte';
	import {
		GROUP_ORDER,
		MODE_FILTERS,
		filterByMode,
		groupDeployments,
		moneyMetricNote,
		moneyMetricText,
		portfolioHeaderMetrics,
		portfolioRow,
		strategyIdentityIndex,
		type ModeFilter,
		type StrategyIdentity
	} from '$lib/deployment-portfolio';
	import { listAllDeployments, listDeploymentsPage, type Deployment } from '$lib/deployments';
	import FleetControls from '$lib/FleetControls.svelte';
	import { lifecycleContractNote } from '$lib/lifecycle-contract';
	import { listStrategies } from '$lib/strategies';

	const PAGE_SIZE = 50;
	const MONEY_METRICS = [
		{ label: 'Allocated capital', key: 'allocated' },
		{ label: 'Performance equity', key: 'equity' },
		{ label: 'Gross exposure', key: 'exposure' }
	] as const;

	let pageRows = $state<Deployment[]>([]);
	let hasMore = $state(false);
	let offset = $state(0);
	let pageCursors = $state<Record<number, string>>({});
	let pageFingerprint = $state<string | null>(null);
	/** Distinguishes a truly empty inventory from an exhausted trailing page. */
	let everLoaded = $state(false);
	let listLoading = $state(true);
	let listError = $state<string | null>(null);

	let filter = $state<ModeFilter>('all');
	let inventory = $state<Deployment[] | null>(null);
	let inventoryError = $state<string | null>(null);
	let names = $state<ReadonlyMap<string, StrategyIdentity>>(new Map());

	const visibleRows = $derived(filterByMode(pageRows, filter));
	const groups = $derived(groupDeployments(visibleRows));
	const metrics = $derived(inventory === null ? null : portfolioHeaderMetrics(inventory, filter));
	/** One reason shown once when every money metric is unavailable for the same cause. */
	const sharedMoneyNote = $derived.by((): string | null => {
		if (metrics === null) return null;
		const notes = MONEY_METRICS.map((item) => moneyMetricNote(metrics[item.key]));
		return notes.every((note) => note !== null && note === notes[0]) &&
			MONEY_METRICS.every((item) => metrics[item.key].state === 'unavailable')
			? notes[0]
			: null;
	});
	const filterOptions = $derived(
		MODE_FILTERS.map((option) => ({
			...option,
			live: option.id === 'live',
			count: inventory === null ? undefined : filterByMode(inventory, option.id).length
		}))
	);
	/** Whether the current page is full: a Next control would have rows to show. */
	const canGoNext = $derived(hasMore && pageRows.length > 0);
	const paged = $derived(offset > 0 || hasMore);

	async function loadPage(targetOffset: number): Promise<void> {
		listLoading = true;
		listError = null;
		try {
			const cursor = targetOffset === 0 ? undefined : pageCursors[targetOffset];
			if (targetOffset > 0 && cursor === undefined)
				throw new Error('Inventory continuation is unavailable; restart at page one.');
			const result = await listDeploymentsPage(PAGE_SIZE, 0, { cursor });
			if (
				result.fingerprint === null ||
				(targetOffset > 0 && result.fingerprint !== pageFingerprint)
			)
				throw new Error('Inventory changed; restart at page one.');
			if (targetOffset === 0) {
				pageFingerprint = result.fingerprint;
				pageCursors = {};
			}
			if (result.hasMore && result.nextCursor === null)
				throw new Error('Inventory continuation is missing; incomplete.');
			if (result.nextCursor !== null) pageCursors[targetOffset + PAGE_SIZE] = result.nextCursor;
			// Fail closed on a page that claims more while being empty.
			if (result.deployments.length === 0 && result.hasMore) {
				throw new Error('Deployment inventory returned an empty page while claiming more rows.');
			}
			pageRows = result.deployments;
			hasMore = result.hasMore;
			offset = targetOffset;
			if (result.deployments.length > 0) everLoaded = true;
		} catch (caught) {
			pageRows = [];
			hasMore = false;
			listError = caught instanceof Error ? caught.message : 'Could not load deployments.';
		} finally {
			listLoading = false;
		}
	}

	/** Full inventory for header metrics; failure leaves every metric as `—`. */
	async function loadInventory(): Promise<void> {
		inventoryError = null;
		try {
			inventory = await listAllDeployments();
		} catch (caught) {
			inventory = null;
			inventoryError =
				caught instanceof Error ? caught.message : 'The deployment inventory is unavailable.';
		}
	}

	/** Strategy names by id; rows fall back to the captured name on failure. */
	async function loadNames(): Promise<void> {
		try {
			names = strategyIdentityIndex(await listStrategies());
		} catch {
			names = new Map();
		}
	}

	function nextPage(): void {
		if (!canGoNext || listLoading) return;
		void loadPage(offset + PAGE_SIZE);
	}

	function previousPage(): void {
		if (offset === 0 || listLoading) return;
		void loadPage(Math.max(0, offset - PAGE_SIZE));
	}

	onMount(() => {
		void loadPage(0);
		void loadInventory();
		void loadNames();
	});
</script>

<svelte:head><title>Portfolio · ThyTrader</title></svelte:head>

<main>
	<PortfolioWorkspace {inventory} />

	<FleetControls />

	<div class="bots-head">
		<div>
			<h2 id="all-bots">All bots</h2>
			<p class="bots-lede">
				Every bot on this workstation. Each runs the rules its strategy had when it started, with
				its own capital; open one for orders, fills, and why it traded.
			</p>
		</div>
		<div class="bots-actions">
			<Segmented
				label="Show bots by mode"
				options={filterOptions}
				value={filter}
				onchange={(next) => (filter = next)}
				testId="portfolio-filter"
			/>
			<a class="btn" href={resolve('/strategies')}>Start a deployment</a>
		</div>
	</div>

	<PreflightPanel />

	<section class="card idbar" aria-label="Bot summary" data-testid="portfolio-metrics">
		<div class="counts">
			<div class="metric">
				<div class="l">Running</div>
				<div class="v" data-testid="metric-running">{metrics?.running ?? '—'}</div>
			</div>
			<div class="metric">
				<div class="l">Paused</div>
				<div class="v" data-testid="metric-paused">{metrics?.paused ?? '—'}</div>
			</div>
			<div class="metric">
				<div class="l">Needs attention</div>
				<div class="v" class:warn={(metrics?.attention ?? 0) > 0} data-testid="metric-attention">
					{metrics?.attention ?? '—'}
				</div>
			</div>
		</div>
		<div class="money">
			{#each MONEY_METRICS as item (item.key)}
				{@const metric = metrics?.[item.key] ?? null}
				<div class="metric">
					<div class="l">{item.label}</div>
					<div class="v" data-testid="metric-{item.key}">
						{metric === null ? '—' : moneyMetricText(metric)}
					</div>
					{#if metric !== null && sharedMoneyNote === null && moneyMetricNote(metric) !== null}
						<div class="n">{moneyMetricNote(metric)}</div>
					{/if}
				</div>
			{/each}
			{#if sharedMoneyNote !== null}
				<p class="n shared">{sharedMoneyNote}</p>
			{/if}
		</div>
		{#if inventoryError !== null}
			<p class="inventory-error" role="status">
				Summary unavailable ({inventoryError}); the list below still loads page by page.
			</p>
		{/if}
		<p class="coming" data-testid="portfolio-coming">
			Bots started by a portfolio run as its sleeves under its shared limits; every other bot below
			runs on its own capital.
		</p>
	</section>

	{#if listLoading}
		<section class="loading-card" aria-label="Loading deployments">
			<div class="skeleton wide"></div>
			<div class="skeleton"></div>
		</section>
	{:else if listError}
		<div class="error-banner" role="alert">
			<div>
				<strong>Couldn't load deployments</strong>
				<p>{listError}</p>
			</div>
			<button type="button" onclick={() => void loadPage(offset)}>Try again</button>
		</div>
	{:else if pageRows.length === 0 && !everLoaded}
		<section class="empty-state">
			<h2>No bots yet</h2>
			<p>
				Pick a strategy and start it from its Run stage, or place a one-off order on Trade. Bots
				started here or by an agent appear here.
			</p>
			<a class="btn" href={resolve('/strategies')}>Start a deployment</a>
		</section>
	{:else if pageRows.length === 0}
		<section class="empty-state" data-testid="trailing-empty-page">
			<h2>No deployments on this page</h2>
			<p>
				Rows past here were removed from the inventory while you were paging. Go back a page or
				return to the first page.
			</p>
			<button type="button" class="btn" onclick={() => void loadPage(0)}>
				Back to first page
			</button>
		</section>
	{:else}
		{#if paged && filter !== 'all'}
			<p class="page-note">The filter applies to the bots on this page.</p>
		{/if}
		{#if visibleRows.length === 0}
			<p class="empty-filter" data-testid="empty-filter">
				No {filter === 'live' ? 'live' : 'paper'} bots on this page.
			</p>
		{/if}
		{#each GROUP_ORDER as group (group.key)}
			{#if groups[group.key].length > 0}
				<section class="group" aria-labelledby="group-{group.key}" data-group={group.key}>
					<h2 class="group-heading" id="group-{group.key}">
						{group.label} <span class="group-count">{groups[group.key].length}</span>
					</h2>
					<div class="card rows">
						<div class="bot-row head" aria-hidden="true">
							<div>Bot</div>
							<div>Mode</div>
							<div>Market</div>
							<div>Position</div>
							<div class="num">PnL</div>
							<div class="right">Status</div>
						</div>
						<ul role="list">
							{#each groups[group.key] as deployment (deployment.id)}
								{@render row(deployment)}
							{/each}
						</ul>
					</div>
				</section>
			{/if}
		{/each}
		<nav class="inventory-pager" aria-label="Deployment inventory pagination">
			<span
				>Showing {pageRows.length} deployment{pageRows.length === 1 ? '' : 's'} from {offset +
					1}</span
			>
			<button
				type="button"
				class="btn"
				onclick={previousPage}
				disabled={listLoading || offset === 0}
				aria-label="Previous deployment page">Previous</button
			>
			<button
				type="button"
				class="btn"
				onclick={nextPage}
				disabled={listLoading || !canGoNext}
				aria-label="Next deployment page">Next</button
			>
		</nav>
	{/if}
</main>

{#snippet row(deployment: Deployment)}
	{@const item = portfolioRow(deployment, names)}
	<li data-testid="bot-row" data-mode={item.mode}>
		<a
			class="bot-row"
			href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
			aria-label="Open {item.name}{item.rules ? ` (${item.rules})` : ''}, {item.modeLabel === 'LIVE'
				? 'live'
				: 'paper'}, {item.market}, {item.status.toLowerCase()}"
		>
			<div class="who">
				<div class="name">
					{item.name}
					{#if item.rules}<span class="faint">{item.rules}</span>{/if}
				</div>
				{#if item.note}
					<div
						class="note"
						class:problem={deployment.mismatch_detail !== null ||
							deployment.daily_loss_latched ||
							deployment.drawdown_latched}
						class:warn={item.readOnly}
					>
						{item.note}
					</div>
				{/if}
			</div>
			<div>
				<span class="chip" class:paper={item.mode === 'paper'} class:live={item.mode === 'live'}
					>{item.modeLabel}</span
				>
			</div>
			<div>{item.market} <span class="faint">· {item.clock}</span></div>
			<div>
				<div>{item.position}</div>
				<div class="faint small">{item.protection}</div>
			</div>
			<div
				class="num"
				class:pos={item.pnlTone === 'pos'}
				class:neg={item.pnlTone === 'neg'}
				class:muted={item.pnlTone === 'muted'}
			>
				{item.pnl}
			</div>
			<div class="right muted">{item.status}</div>
		</a>
		{#if item.readOnly}
			<p class="sr-only">{lifecycleContractNote(deployment)}</p>
		{/if}
	</li>
{/snippet}

<style>
	.bots-head {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 12px 16px;
		margin: 32px 0 12px;
		padding-top: 20px;
		border-top: 1px solid var(--line);
	}
	.bots-head h2 {
		font-size: var(--fs-lg);
	}
	.bots-lede {
		margin: 4px 0 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.bots-actions {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-left: auto;
	}
	.idbar {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: 16px 36px;
		margin-bottom: var(--space-2);
		padding: 16px 18px;
	}
	.counts,
	.money {
		display: flex;
		flex-wrap: wrap;
		gap: 16px 28px;
	}
	.money {
		margin-left: auto;
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
	.shared {
		flex-basis: 100%;
		margin: 0;
	}
	.n {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.metric .n {
		max-width: 26ch;
		margin-top: 2px;
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.warn {
		color: var(--warn);
	}
	.coming,
	.inventory-error {
		flex-basis: 100%;
		margin: 0;
		padding-top: 12px;
		border-top: 1px solid var(--line);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.inventory-error {
		color: var(--warn);
	}
	.page-note,
	.empty-filter {
		margin: 18px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.group-heading {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 22px 0 8px;
		color: var(--faint);
		font-size: var(--fs-sm);
		font-weight: 500;
	}
	.group-count {
		color: var(--muted);
	}
	.rows ul {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.bot-row {
		display: grid;
		grid-template-columns:
			minmax(0, 2.2fr) 80px minmax(0, 1.2fr) minmax(0, 1.5fr) minmax(0, 0.9fr)
			minmax(0, 0.8fr);
		align-items: center;
		gap: 12px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
		color: var(--text);
		text-decoration: none;
	}
	.rows li:last-child .bot-row {
		border-bottom: 0;
	}
	a.bot-row:hover {
		background: var(--hover);
	}
	a.bot-row:focus-visible {
		outline-offset: -2px;
	}
	.bot-row.head {
		padding-top: 10px;
		padding-bottom: 10px;
		color: var(--faint);
		font-size: 11.5px;
	}
	.name {
		font-weight: 500;
	}
	.note {
		margin-top: 2px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.note.problem {
		color: var(--neg);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.faint {
		color: var(--faint);
	}
	.muted {
		color: var(--muted);
	}
	.pos {
		color: var(--pos);
	}
	.neg {
		color: var(--neg);
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.right {
		text-align: right;
	}
	.inventory-pager {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 22px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	@media (max-width: 900px) {
		.money {
			margin-left: 0;
		}
		.bot-row {
			grid-template-columns: minmax(0, 1fr) auto;
		}
		.bot-row.head {
			display: none;
		}
		.bot-row > :nth-child(3),
		.bot-row > :nth-child(4) {
			grid-column: 1 / -1;
		}
	}
</style>
