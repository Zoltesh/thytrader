<script lang="ts">
	/**
	 * Portfolio backtest tab: run (async job with progress), then the combined
	 * result — metrics, equity vs the equal-weight basket, contribution,
	 * correlation, overlap, and the disclosures verbatim — plus earlier runs.
	 *
	 * Fee fields prefill from the Coinbase fee-tier suggestion without ever
	 * overwriting an edit. Results are simulated from candles, never fills.
	 *
	 * This component owns the run, polling and selection state; the run card,
	 * result and earlier runs render in their own components.
	 */
	import { onDestroy } from 'svelte';
	import {
		fetchFeeProfile,
		formatFeeProfileAsOf,
		formatResearchFeeSourceChip,
		readResearchFeeSuggestion,
		researchFeeFieldSource,
		shouldPrefillResearchFeeRates,
		type ResearchFeeSuggestion
	} from '$lib/fees';
	import {
		CONFLICT_RELOADED,
		backtestProblems,
		errorText,
		fetchPortfolioBacktest,
		fetchPortfolioBacktestJob,
		isJobActive,
		isRevisionConflict,
		listPortfolioBacktestJobs,
		listPortfolioBacktests,
		submitPortfolioBacktest,
		type BacktestProblem,
		type Portfolio,
		type PortfolioBacktestDetail,
		type PortfolioBacktestJob,
		type PortfolioBacktestListing
	} from '$lib/portfolios';
	import BacktestResult from './BacktestResult.svelte';
	import BacktestRunCard from './BacktestRunCard.svelte';
	import BacktestRuns from './BacktestRuns.svelte';

	let {
		portfolio,
		onconflict
	}: {
		portfolio: Portfolio;
		onconflict: () => Promise<void>;
	} = $props();

	const POLL_MS = 1000;
	const DECIMAL = /^\d+(?:\.\d+)?$/;

	let maker = $state('');
	let taker = $state('');
	let slippage = $state('10');
	let spread = $state('');
	let feeTouched = $state(false);
	let latestSuggestion = $state<ResearchFeeSuggestion | null>(null);
	let appliedSuggestion = $state<ResearchFeeSuggestion | null>(null);
	let feeLoading = $state(false);

	let listings = $state<PortfolioBacktestListing[]>([]);
	let listingsError = $state<string | null>(null);
	let selected = $state<string | null>(null);
	let detail = $state<PortfolioBacktestDetail | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);

	let job = $state<PortfolioBacktestJob | null>(null);
	let submitting = $state(false);
	let runError = $state<string | null>(null);
	let problems = $state<BacktestProblem[]>([]);

	let loadedFor = '';
	let generation = 0;
	let pollTimer: ReturnType<typeof setTimeout> | null = null;

	const feeSource = $derived(
		researchFeeFieldSource({
			makerFeeRate: maker,
			takerFeeRate: taker,
			applied: appliedSuggestion,
			latest: latestSuggestion,
			loading: feeLoading
		})
	);
	const feeChip = $derived(
		formatResearchFeeSourceChip(
			feeSource,
			formatFeeProfileAsOf((appliedSuggestion ?? latestSuggestion)?.fetchedAt ?? '')
		)
	);
	const formProblem = $derived.by((): string | null => {
		if (portfolio.sleeves.length === 0) return 'Add a sleeve first.';
		if (!DECIMAL.test(maker.trim()) || !DECIMAL.test(taker.trim()))
			return 'Enter maker and taker fee rates as decimals (0.004 = 0.4%).';
		if (!DECIMAL.test(slippage.trim())) return 'Slippage is a number of basis points.';
		if (spread.trim() !== '' && !DECIMAL.test(spread.trim()))
			return 'Spread stress is a number of basis points.';
		return null;
	});
	const running = $derived(job !== null && isJobActive(job));

	$effect(() => {
		const id = portfolio.portfolio_id;
		if (loadedFor === id) return;
		loadedFor = id;
		generation += 1;
		stopPolling();
		listings = [];
		selected = null;
		detail = null;
		job = null;
		runError = null;
		problems = [];
		void initialLoad(id, generation);
	});

	onDestroy(() => {
		generation += 1;
		stopPolling();
	});

	function stopPolling(): void {
		if (pollTimer !== null) clearTimeout(pollTimer);
		pollTimer = null;
	}

	async function initialLoad(portfolioId: string, token: number): Promise<void> {
		void loadFeeSuggestion(token);
		await loadListings(portfolioId, token, null);
		try {
			const jobs = await listPortfolioBacktestJobs(portfolioId, 5);
			if (token !== generation) return;
			const active = jobs.find((item) => isJobActive(item)) ?? null;
			if (active !== null) {
				job = active;
				schedulePoll(portfolioId, token);
			}
		} catch {
			// Job history is a convenience; the run form still works.
		}
	}

	async function loadFeeSuggestion(token: number): Promise<void> {
		feeLoading = true;
		try {
			const profile = await fetchFeeProfile();
			if (token !== generation) return;
			const suggestion = readResearchFeeSuggestion(profile);
			latestSuggestion = suggestion;
			if (
				suggestion !== null &&
				shouldPrefillResearchFeeRates({
					makerFeeRate: maker,
					takerFeeRate: taker,
					touched: feeTouched,
					suggestion
				})
			) {
				maker = suggestion.makerFeeRate;
				taker = suggestion.takerFeeRate;
				appliedSuggestion = suggestion;
			}
		} catch {
			if (token === generation) latestSuggestion = null;
		} finally {
			if (token === generation) feeLoading = false;
		}
	}

	async function loadListings(
		portfolioId: string,
		token: number,
		prefer: string | null
	): Promise<void> {
		listingsError = null;
		try {
			const rows = await listPortfolioBacktests(portfolioId, 10);
			if (token !== generation) return;
			listings = rows;
			const target = prefer ?? rows[0]?.result_fingerprint ?? null;
			if (target !== null) await select(target, token);
		} catch (caught) {
			if (token !== generation) return;
			listingsError = errorText(caught, 'Earlier portfolio backtests are unavailable.');
		}
	}

	async function select(fingerprint: string, token: number = generation): Promise<void> {
		selected = fingerprint;
		detailLoading = true;
		detailError = null;
		try {
			const loaded = await fetchPortfolioBacktest(portfolio.portfolio_id, fingerprint, 600);
			if (token !== generation || selected !== fingerprint) return;
			detail = loaded;
		} catch (caught) {
			if (token !== generation) return;
			detailError = errorText(caught, 'This portfolio backtest could not be loaded.');
		} finally {
			if (token === generation) detailLoading = false;
		}
	}

	function schedulePoll(portfolioId: string, token: number): void {
		stopPolling();
		pollTimer = setTimeout(() => void poll(portfolioId, token), POLL_MS);
	}

	async function poll(portfolioId: string, token: number): Promise<void> {
		const current = job;
		if (current === null || token !== generation) return;
		try {
			const next = await fetchPortfolioBacktestJob(portfolioId, current.job_id);
			if (token !== generation) return;
			job = next;
			if (isJobActive(next)) {
				schedulePoll(portfolioId, token);
				return;
			}
			if (next.status === 'completed' && next.result_fingerprint !== null) {
				await loadListings(portfolioId, token, next.result_fingerprint);
			}
		} catch (caught) {
			if (token !== generation) return;
			runError = errorText(caught, 'Lost track of the portfolio backtest; refresh to check it.');
		}
	}

	async function run(): Promise<void> {
		if (formProblem !== null || running) return;
		submitting = true;
		runError = null;
		problems = [];
		const token = generation;
		const portfolioId = portfolio.portfolio_id;
		try {
			const accepted = await submitPortfolioBacktest(portfolioId, {
				revision: portfolio.revision,
				maker_fee_rate: maker.trim(),
				taker_fee_rate: taker.trim(),
				fixed_slippage_bps: slippage.trim(),
				...(spread.trim() === '' ? {} : { spread_bps: spread.trim() })
			});
			if (token !== generation) return;
			job = accepted.job;
			schedulePoll(portfolioId, token);
		} catch (caught) {
			if (token !== generation) return;
			if (isRevisionConflict(caught)) {
				await onconflict();
				runError = CONFLICT_RELOADED;
				return;
			}
			problems = backtestProblems(caught);
			runError = errorText(caught, 'The portfolio backtest could not start.');
		} finally {
			submitting = false;
		}
	}
</script>

<BacktestRunCard
	bind:maker
	bind:taker
	bind:slippage
	bind:spread
	{feeChip}
	{formProblem}
	{submitting}
	{running}
	{job}
	{runError}
	{problems}
	onfeeedit={() => (feeTouched = true)}
	onrun={() => void run()}
/>

{#if listingsError}
	<p class="problem small">{listingsError}</p>
{/if}

{#if detailLoading && detail === null}
	<section class="loading-card" aria-label="Loading portfolio backtest">
		<div class="skeleton wide"></div>
	</section>
{:else if detailError}
	<p class="problem small" role="alert">{detailError}</p>
{:else if detail !== null}
	<BacktestResult {detail} />
{:else if listings.length === 0 && job === null}
	<section class="empty-state">
		<h2>No portfolio backtests yet</h2>
		<p>
			Run one to see the sleeves combined: net return, drawdown, idle capital, what each sleeve
			contributed, how they correlate, and an equal-weight buy-and-hold basket to beat.
		</p>
	</section>
{/if}

{#if listings.length > 0}
	<BacktestRuns {listings} {selected} onselect={(fingerprint) => void select(fingerprint)} />
{/if}

<style>
	.small {
		font-size: var(--fs-sm);
	}
	.problem {
		color: var(--neg);
	}
	.empty-state {
		margin-top: 16px;
	}
</style>
