<script lang="ts">
	/**
	 * Home (ADR 0084): connection status and primary actions, four KPI tiles, the
	 * portfolio value chart, Needs attention, Your bots, Holdings, the fee tier,
	 * and a Data health disclosure.
	 *
	 * Every card reads its own sources through a `Resource`, so a slow or failing
	 * endpoint (the data catalog can take ~20 s; the Coinbase portfolio is slow)
	 * never blocks the rest of Home, and each card has its own skeleton and
	 * retry. Figures come only from existing endpoints; anything unknown is `—`
	 * with the reason.
	 */
	import { onMount, tick } from 'svelte';
	import { resolve } from '$app/paths';
	import PortfolioChart from '$lib/PortfolioChart.svelte';
	import { fetchCoinbaseCredentialsStatus } from '$lib/credentials';
	import { strategyIdentityIndex } from '$lib/deployment-portfolio';
	import { listAllDeployments } from '$lib/deployments';
	import { fetchFeeProfile } from '$lib/fees';
	import AttentionCard, { type AttentionSourceStatus } from '$lib/home/AttentionCard.svelte';
	import BotsCard from '$lib/home/BotsCard.svelte';
	import DataHealth from '$lib/home/DataHealth.svelte';
	import FeeTierCard from '$lib/home/FeeTierCard.svelte';
	import FuturesCard from '$lib/home/FuturesCard.svelte';
	import PreflightPanel from '$lib/PreflightPanel.svelte';
	import HoldingsCard from '$lib/home/HoldingsCard.svelte';
	import KpiTile from '$lib/home/KpiTile.svelte';
	import {
		DEFAULT_HOME_CHART_RANGE,
		homeChartRangeOption,
		type HomeChartRange
	} from '$lib/home/history-range';
	import {
		attentionBotCount,
		backfillCandidates,
		datasetAttention,
		datasetProblemIndex,
		deploymentAttention,
		researchAttention,
		setupAttention,
		sortAttention
	} from '$lib/home/home-attention';
	import {
		RESEARCH_CHECK_LIMIT,
		fetchChartHistory,
		fetchDataCatalog,
		fetchIngestionStates,
		fetchPortfolio,
		fetchPortfolioHistory,
		fetchRecentResearchJobs
	} from '$lib/home/home-data';
	import {
		availableToTradeTile,
		botsTile,
		connectionLine,
		liveExposureTile,
		portfolioValueTile
	} from '$lib/home/home-kpis';
	import { Resource } from '$lib/home/resource.svelte';
	import { listStrategies } from '$lib/strategies';
	import { fetchRiskPolicySnapshot } from '$lib/workspace-data';

	let chartRange = $state<HomeChartRange>(DEFAULT_HOME_CHART_RANGE);
	let nowMs = $state(Date.now());
	let dataHealthOpen = $state(false);

	const portfolio = new Resource(fetchPortfolio);
	const history24h = new Resource(() => fetchPortfolioHistory('24h'));
	const chart = new Resource(() => fetchChartHistory(homeChartRangeOption(chartRange)));
	const deployments = new Resource(() => listAllDeployments());
	const strategies = new Resource(() => listStrategies());
	const riskPolicy = new Resource(fetchRiskPolicySnapshot);
	const credentials = new Resource(fetchCoinbaseCredentialsStatus);
	const catalog = new Resource(fetchDataCatalog);
	const fees = new Resource(fetchFeeProfile);
	const ingestion = new Resource(() =>
		fetchIngestionStates(backfillCandidates(catalog.state.data?.payload.datasets ?? []))
	);
	const research = new Resource(() => fetchRecentResearchJobs(strategies.state.data ?? []));

	const names = $derived(strategyIdentityIndex(strategies.state.data ?? []));
	const current = $derived(portfolio.state.data);
	const status = $derived(
		connectionLine({ portfolio: portfolio.state, credentials: credentials.state, nowMs })
	);

	const botItems = $derived(
		deployments.state.data === null ? [] : deploymentAttention(deployments.state.data, names)
	);
	const setupItems = $derived(
		deployments.state.data === null
			? []
			: setupAttention({
					deployments: deployments.state.data,
					credentials: credentials.state.data,
					riskPolicy: riskPolicy.state.data
				})
	);
	const dataItems = $derived(
		catalog.state.data === null
			? []
			: datasetAttention({
					rows: catalog.state.data.payload.datasets,
					ingestion: ingestion.state.data?.states ?? {},
					strategies: strategies.state.data ?? [],
					nowMs
				})
	);
	const datasetProblems = $derived(datasetProblemIndex(dataItems));
	const researchItems = $derived(
		research.state.data === null ? [] : researchAttention(research.state.data, nowMs)
	);
	const attentionItems = $derived(
		sortAttention([...setupItems, ...botItems, ...dataItems, ...researchItems])
	);

	const liveBotsExist = $derived(
		(deployments.state.data ?? []).some(
			(deployment) => deployment.mode === 'live' && deployment.status !== 'stopped'
		)
	);

	const sources = $derived.by((): AttentionSourceStatus[] => [
		botsSource(),
		setupSource(),
		datasetsSource(),
		researchSource()
	]);

	function botsSource(): AttentionSourceStatus {
		const base = { id: 'bots' as const, label: 'bots' };
		if (deployments.state.status === 'loading') return { ...base, state: 'loading', note: null };
		if (deployments.state.status === 'error') {
			return {
				...base,
				state: 'error',
				note: deployments.state.error,
				retry: () => void deployments.reload()
			};
		}
		return { ...base, state: 'ready', note: null };
	}

	function setupSource(): AttentionSourceStatus {
		const base = { id: 'setup' as const, label: 'credentials and risk policy' };
		if (deployments.state.status === 'ready' && !liveBotsExist) {
			return { ...base, state: 'ready', note: null };
		}
		if (deployments.state.status === 'error' && deployments.state.data === null) {
			// Whether live bots exist decides this check, so it cannot pass without the bot list.
			return {
				...base,
				state: 'error',
				note: 'the bot list could not be loaded.',
				retry: () => void deployments.reload()
			};
		}
		const failed = [credentials, riskPolicy].filter((source) => source.state.status === 'error');
		if (failed.length > 0) {
			return {
				...base,
				state: 'error',
				note: 'the status could not be read.',
				retry: () => {
					for (const source of failed) void source.reload();
				}
			};
		}
		if (
			deployments.state.status === 'loading' ||
			credentials.state.status === 'loading' ||
			riskPolicy.state.status === 'loading'
		) {
			return { ...base, state: 'loading', note: null };
		}
		return { ...base, state: 'ready', note: null };
	}

	function datasetsSource(): AttentionSourceStatus {
		const base = { id: 'datasets' as const, label: 'watched datasets' };
		const report = catalog.state;
		if (report.status === 'loading') {
			return {
				...base,
				state: 'loading',
				note: 'Checking watched datasets… the data catalog can take about 20 seconds.'
			};
		}
		if (report.status === 'error') {
			return { ...base, state: 'error', note: report.error, retry: () => void loadCatalog() };
		}
		if (ingestion.state.status === 'loading') {
			return { ...base, state: 'loading', note: 'Checking backfill progress…' };
		}
		const notes = [...report.data.partial_result_warnings];
		if (ingestion.state.status === 'error') {
			notes.push(`Backfill progress could not be read (${ingestion.state.error}).`);
		} else if (ingestion.state.data !== null) {
			const scan = ingestion.state.data;
			if (scan.unreadable > 0) {
				notes.push(
					`Backfill progress could not be read for ${scan.unreadable} ${scan.unreadable === 1 ? 'dataset' : 'datasets'}.`
				);
			}
			if (scan.skipped > 0) {
				notes.push(
					`${scan.skipped} more ${scan.skipped === 1 ? 'backfill was' : 'backfills were'} not checked.`
				);
			}
		}
		return notes.length > 0
			? { ...base, state: 'partial', note: notes.join(' ') }
			: { ...base, state: 'ready', note: null };
	}

	function researchSource(): AttentionSourceStatus {
		const base = { id: 'research' as const, label: 'research jobs' };
		if (strategies.state.status === 'error' && strategies.state.data === null) {
			return {
				...base,
				state: 'error',
				note: `the strategy library could not be read (${strategies.state.error}).`,
				retry: () => void loadStrategies()
			};
		}
		if (strategies.state.status === 'loading' || research.state.status === 'loading') {
			return { ...base, state: 'loading', note: null };
		}
		if (research.state.status === 'error') {
			return {
				...base,
				state: 'error',
				note: research.state.error,
				retry: () => void research.reload()
			};
		}
		const scan = research.state.data;
		const notes: string[] = [];
		if (scan.unchecked > 0) {
			notes.push(`checked the ${RESEARCH_CHECK_LIMIT} most recently updated strategies.`);
		}
		if (scan.unreadable > 0) notes.push(`${scan.unreadable} job lists could not be read.`);
		return notes.length > 0
			? { ...base, state: 'partial', note: notes.join(' ') }
			: { ...base, state: 'ready', note: null };
	}

	const valueTile = $derived(
		portfolioValueTile({ portfolio: portfolio.state, history: history24h.state, nowMs })
	);
	const availableTile = $derived(
		availableToTradeTile({
			portfolio: portfolio.state,
			riskPolicy: riskPolicy.state,
			deployments: deployments.state
		})
	);
	const exposureTile = $derived(liveExposureTile(deployments.state));
	const botCountTile = $derived(
		botsTile(
			deployments.state,
			deployments.state.data === null ? null : attentionBotCount(botItems)
		)
	);

	async function loadCatalog(): Promise<void> {
		await catalog.reload();
		if (catalog.state.status === 'ready') await ingestion.reload();
	}

	async function loadStrategies(): Promise<void> {
		await strategies.reload();
		if (strategies.state.status === 'ready') await research.reload();
	}

	function refreshBalances(): void {
		void portfolio.reload();
		void history24h.reload();
	}

	function retryAvailable(): void {
		if (riskPolicy.state.status === 'error') void riskPolicy.reload();
		if (portfolio.state.status === 'error') void portfolio.reload();
	}

	function setChartRange(next: HomeChartRange): void {
		if (next === chartRange) return;
		chartRange = next;
		void chart.reload();
	}

	async function showDataHealth(): Promise<void> {
		dataHealthOpen = true;
		await tick();
		const target = document.getElementById('data-health');
		if (target === null) return;
		const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
		target.querySelector('summary')?.focus({ preventScroll: true });
	}

	onMount(() => {
		void portfolio.reload();
		void history24h.reload();
		void chart.reload();
		void deployments.reload();
		void loadStrategies();
		void riskPolicy.reload();
		void credentials.reload();
		void loadCatalog();
		void fees.reload();
		if (window.location.hash === '#data-health') void showDataHealth();
		const timer = window.setInterval(() => (nowMs = Date.now()), 15_000);
		return () => window.clearInterval(timer);
	});
</script>

<svelte:head><title>Home · ThyTrader</title></svelte:head>

<main class="home">
	<header class="home-head">
		<div class="titles">
			<h1>Home</h1>
			<p class="status-line tone-{status.tone}" data-testid="connection-status">
				<span class="status-dot" aria-hidden="true"></span>
				<span>{status.text}</span>
				{#if portfolio.state.status === 'error' && current === null}
					<button type="button" class="btn ghost retry" onclick={refreshBalances}>Retry</button>
				{/if}
			</p>
		</div>
		<div class="actions">
			<a class="btn" href={resolve('/trade')}>New order</a>
			<a class="btn primary" href={resolve('/strategies')}>New strategy</a>
		</div>
	</header>

	{#if current !== null && current.demo}
		{#if current.assets.length === 0}
			<section class="empty-state onboarding" aria-label="Getting started">
				<h2>Connect Coinbase to see your portfolio</h2>
				<p>
					ThyTrader is showing an empty demo until Coinbase Advanced Trade credentials are
					configured on the server. Then Home shows your real spot balances, and Strategies is where
					your first strategy starts.
				</p>
				<div class="onboarding-links">
					<a class="btn primary" href={resolve('/settings')}>Add credentials in Settings</a>
					<a class="btn" href={resolve('/strategies')}>Explore Strategies</a>
				</div>
			</section>
		{:else}
			<div class="demo-banner" data-testid="demo-banner">
				<div><span class="demo-dot" aria-hidden="true"></span><strong>Demo data</strong></div>
				<p>
					These balances are a deterministic demo, not your Coinbase account.
					<a href={resolve('/settings')}>Add Coinbase credentials in Settings</a> to see your live balances.
				</p>
			</div>
		{/if}
	{/if}

	<section class="kpis" aria-labelledby="kpis-title">
		<h2 id="kpis-title" class="sr-only">Key figures</h2>
		<KpiTile
			label="Portfolio value"
			view={valueTile}
			testId="kpi-portfolio-value"
			onretry={refreshBalances}
		/>
		<KpiTile
			label="Available to trade"
			view={availableTile}
			testId="kpi-available"
			onretry={retryAvailable}
		/>
		<KpiTile
			label="Live exposure"
			view={exposureTile}
			testId="kpi-live-exposure"
			onretry={() => void deployments.reload()}
		/>
		<KpiTile
			label="Bots"
			view={botCountTile}
			testId="kpi-bots"
			onretry={() => void deployments.reload()}
		/>
	</section>

	<div class="grid2">
		<PortfolioChart
			history={chart.state}
			range={chartRange}
			onRangeChange={setChartRange}
			onRetry={() => void chart.reload()}
		/>
		<AttentionCard items={attentionItems} {sources} onDataHealth={() => void showDataHealth()} />
	</div>

	<div class="stack">
		<BotsCard deployments={deployments.state} {names} onretry={() => void deployments.reload()} />
		<HoldingsCard portfolio={portfolio.state} {nowMs} onrefresh={refreshBalances} />
		<FeeTierCard
			fees={fees.state}
			demo={current?.demo === true}
			onretry={() => void fees.reload()}
		/>
		<FuturesCard />
		<PreflightPanel />
		<DataHealth
			catalog={catalog.state}
			problems={datasetProblems}
			bind:open={dataHealthOpen}
			onretry={() => void loadCatalog()}
		/>
	</div>
</main>

<style>
	.home-head {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 12px;
		margin-bottom: var(--space-5);
	}
	.titles {
		min-width: 0;
	}
	.home-head h1 {
		margin: 0;
	}
	.status-line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin: 4px 0 0;
		color: var(--muted);
	}
	.status-dot {
		width: 7px;
		height: 7px;
		flex: none;
		border-radius: 50%;
		background: var(--faint);
	}
	.tone-ok .status-dot {
		background: var(--pos);
	}
	.tone-warn .status-dot {
		background: var(--warn);
	}
	.tone-error {
		color: var(--neg);
	}
	.tone-error .status-dot {
		background: var(--neg);
	}
	.retry {
		min-height: 26px;
		padding: 0 8px;
	}
	.actions {
		display: flex;
		gap: 8px;
		margin-left: auto;
	}
	.onboarding {
		margin-bottom: var(--space-4);
	}
	.onboarding-links {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.demo-banner p a {
		color: var(--accent);
	}
	.kpis {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 12px;
	}
	.grid2 {
		display: grid;
		grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
		align-items: start;
		gap: 16px;
		margin-top: 16px;
	}
	.stack {
		display: grid;
		gap: 16px;
		margin-top: 16px;
	}
	@media (max-width: 1180px) {
		.kpis {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 1000px) {
		.grid2 {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	@media (max-width: 560px) {
		.kpis {
			grid-template-columns: minmax(0, 1fr);
		}
		.actions {
			margin-left: 0;
		}
	}
</style>
