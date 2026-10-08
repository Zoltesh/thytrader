<script lang="ts">
	/**
	 * Portfolio backtest tab: run (async job with progress), then the combined
	 * result — metrics, equity vs the equal-weight basket, contribution,
	 * correlation, overlap, and the disclosures verbatim — plus earlier runs.
	 *
	 * Fee fields prefill from the Coinbase fee-tier suggestion without ever
	 * overwriting an edit. Results are simulated from candles, never fills.
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
	import { formatPercent, subtractDecimalStrings } from '$lib/portfolio';
	import {
		CONFLICT_RELOADED,
		backtestProblems,
		baseAsset,
		coefficientText,
		contributionPoints,
		drawdownPercent,
		durationText,
		errorText,
		fetchPortfolioBacktest,
		fetchPortfolioBacktestJob,
		isJobActive,
		isRevisionConflict,
		jobProgressText,
		listPortfolioBacktestJobs,
		listPortfolioBacktests,
		pairCoefficient,
		quoteText,
		ratioText,
		signedPercent,
		submitPortfolioBacktest,
		utcMinute,
		weightPercent,
		windowText,
		type BacktestProblem,
		type Portfolio,
		type PortfolioBacktestDetail,
		type PortfolioBacktestJob,
		type PortfolioBacktestListing
	} from '$lib/portfolios';
	import PortfolioEquityChart from './PortfolioEquityChart.svelte';

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

	function costsText(costs: PortfolioBacktestDetail['result']['costs']): string {
		const spreadText = costs.spread_bps === '0' ? '' : ` · spread stress ${costs.spread_bps} bps`;
		return `maker ${formatPercent(costs.maker_fee_rate)} · taker ${formatPercent(costs.taker_fee_rate)} · slippage ${costs.fixed_slippage_bps} bps${spreadText}`;
	}

	function tone(fraction: string): 'pos' | 'neg' | '' {
		if (fraction.startsWith('-')) return 'neg';
		return /^0(?:\.0*)?$/.test(fraction) ? '' : 'pos';
	}
</script>

<section class="card run" aria-label="Run a portfolio backtest">
	<div class="run-head">
		<h2>Run a portfolio backtest</h2>
		<span class="faint small"
			>Every sleeve runs the unified backtest model on its own slice of capital over the sleeves'
			common window, using each one's latest complete datasets.</span
		>
	</div>
	<div class="fields">
		<label class="field">
			<span>Maker fee rate</span>
			<input
				type="text"
				inputmode="decimal"
				bind:value={maker}
				oninput={() => (feeTouched = true)}
				placeholder="0.004"
			/>
		</label>
		<label class="field">
			<span>Taker fee rate</span>
			<input
				type="text"
				inputmode="decimal"
				bind:value={taker}
				oninput={() => (feeTouched = true)}
				placeholder="0.006"
			/>
		</label>
		<label class="field">
			<span>Slippage (bps)</span>
			<input type="text" inputmode="decimal" bind:value={slippage} />
		</label>
		<label class="field">
			<span>Spread stress (bps, optional)</span>
			<input type="text" inputmode="decimal" bind:value={spread} placeholder="0" />
		</label>
		<button
			type="button"
			class="btn primary run-button"
			disabled={formProblem !== null || submitting || running}
			onclick={() => void run()}
			>{submitting ? 'Starting…' : running ? 'Running…' : 'Run portfolio backtest'}</button
		>
	</div>
	<div class="run-foot">
		<span class="chip" data-testid="fee-source">{feeChip}</span>
		{#if formProblem !== null}<span class="faint small">{formProblem}</span>{/if}
	</div>
	{#if job !== null}
		<div
			class="progress"
			class:failed={job.status === 'failed' || job.status === 'expired'}
			role="status"
			data-testid="backtest-progress"
		>
			{#if isJobActive(job)}<span class="pulse" aria-hidden="true"></span>{/if}
			<span>{jobProgressText(job)}</span>
			{#if isJobActive(job)}
				<span class="faint small"
					>{job.progress_current} of {job.progress_total} steps · {windowText(
						job.evaluation_start,
						job.evaluation_end
					)}</span
				>
			{/if}
		</div>
	{/if}
	{#if runError}
		<div class="problem-box" role="alert" data-testid="backtest-error">
			<p>{runError}</p>
			{#if problems.length > 0}
				<ul>
					{#each problems as problem, index (index)}
						<li>
							{#if problem.strategy_name}<strong>{problem.strategy_name}:</strong>{/if}
							{problem.message}
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{/if}
</section>

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
	{@const result = detail.result}
	{@const summary = result.summary}
	<section class="card result" aria-label="Portfolio backtest result">
		<div class="card-head">
			<h2>
				All sleeves together · {windowText(result.evaluation_start, result.evaluation_end)}
			</h2>
			<span class="chip">Simulated result (candle-based fills)</span>
		</div>
		<div class="metrics" data-testid="backtest-metrics">
			<div class="metric">
				<div class="l">Net return</div>
				<div class="v {tone(summary.total_return_fraction)}">
					{signedPercent(summary.total_return_fraction)}
				</div>
				<div class="n">{quoteText(summary.final_equity, result.quote_currency)} final</div>
			</div>
			<div class="metric">
				<div class="l">Max drawdown</div>
				<div class="v neg">{drawdownPercent(summary.maximum_drawdown_fraction)}</div>
			</div>
			<div class="metric">
				<div class="l">Best sleeve alone</div>
				<div class="v">{signedPercent(summary.best_sleeve_return_fraction)}</div>
				<div class="n">
					its drawdown {drawdownPercent(summary.best_sleeve_maximum_drawdown_fraction)}
				</div>
			</div>
			<div class="metric">
				<div class="l">Capital idle</div>
				<div class="v">{formatPercent(summary.idle_capital_fraction)}</div>
			</div>
			<div class="metric">
				<div class="l">Equal-weight basket</div>
				<div class="v {tone(result.basket.total_return_fraction)}">
					{signedPercent(result.basket.total_return_fraction)}
				</div>
				<div class="n">
					buy &amp; hold, drawdown {drawdownPercent(result.basket.maximum_drawdown_fraction)}
				</div>
			</div>
			<div class="metric">
				<div class="l">Sharpe</div>
				<div class="v">{ratioText(result.metrics.sharpe)}</div>
				<div class="n">rf 0 · {summary.trade_count} trades</div>
			</div>
		</div>
		<PortfolioEquityChart
			points={result.equity_curve}
			currency={result.quote_currency}
			ariaLabel="Combined portfolio equity {signedPercent(
				summary.total_return_fraction
			)} against the equal-weight basket {signedPercent(result.basket.total_return_fraction)}"
		/>
		{#if detail.equity_curve_downsampled}
			<p class="faint small">
				Chart thinned to {result.equity_curve.length} of {detail.equity_curve_points} points for display;
				the stored result keeps every point.
			</p>
		{/if}
		<p class="faint small">
			Costs: {costsText(result.costs)} · revision {result.portfolio_revision}
		</p>
	</section>

	<section class="card" aria-label="What each sleeve contributed">
		<div class="card-head"><h2>What each sleeve contributed</h2></div>
		<div class="table-wrap">
			<table data-testid="contribution-table">
				<thead>
					<tr>
						<th scope="col">Sleeve</th>
						<th scope="col">Weight</th>
						<th scope="col">Contribution</th>
						<th scope="col">Alone</th>
						<th scope="col">Trades</th>
						<th scope="col">Max DD alone</th>
						<th scope="col">Correlation to rest</th>
					</tr>
				</thead>
				<tbody>
					{#each result.sleeves as sleeve (sleeve.sleeve_id)}
						<tr>
							<td
								>{sleeve.strategy_name}
								<span class="faint">{baseAsset(sleeve.product_id)}</span></td
							>
							<td>{weightPercent(sleeve.weight_fraction)}</td>
							<td class={tone(sleeve.contribution_fraction)}
								>{contributionPoints(sleeve.contribution_fraction)}</td
							>
							<td class={tone(sleeve.total_return_fraction)}
								>{signedPercent(sleeve.total_return_fraction)}</td
							>
							<td>{sleeve.trade_count}</td>
							<td>{drawdownPercent(sleeve.maximum_drawdown_fraction)}</td>
							<td>{coefficientText(sleeve.correlation_to_rest)}</td>
						</tr>
					{/each}
					<tr>
						<td>Cash (reserve and unallocated)</td>
						<td>{weightPercent(subtractDecimalStrings('1', summary.allocated_fraction))}</td>
						<td>0.00 pts</td>
						<td>—</td>
						<td>—</td>
						<td>—</td>
						<td>—</td>
					</tr>
				</tbody>
			</table>
		</div>
	</section>

	<div class="grid2">
		<section class="card" aria-label="Correlation">
			<div class="card-head">
				<h2>Correlation of sleeve returns</h2>
				<span class="faint small"
					>{result.correlation.observations} returns on a {durationText(
						result.correlation.return_clock_seconds
					)} clock</span
				>
			</div>
			{#if result.sleeves.length < 2}
				<p class="pad faint">Correlation needs at least two sleeves.</p>
			{:else}
				<div class="table-wrap">
					<table data-testid="correlation-table">
						<thead>
							<tr>
								<th scope="col"><span class="sr-only">Sleeve</span></th>
								{#each result.sleeves as column (column.sleeve_id)}
									<th scope="col">{column.strategy_name}</th>
								{/each}
							</tr>
						</thead>
						<tbody>
							{#each result.sleeves as row (row.sleeve_id)}
								<tr>
									<th scope="row" class="row-head">{row.strategy_name}</th>
									{#each result.sleeves as column (column.sleeve_id)}
										<td
											>{row.sleeve_id === column.sleeve_id
												? '—'
												: coefficientText(
														pairCoefficient(result.correlation, row.sleeve_id, column.sleeve_id)
													)}</td
										>
									{/each}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
		<section class="card body" aria-label="Overlap and basket">
			<h2>Overlap</h2>
			<div class="check">
				<span class="muted">Two sleeves long the same asset</span>
				<span data-testid="overlap-same-asset"
					>{formatPercent(result.overlap.same_asset_fraction)} of the time</span
				>
			</div>
			<div class="check">
				<span class="muted">Any two sleeves long together</span>
				<span>{formatPercent(result.overlap.long_together_fraction)} of the time</span>
			</div>
			{#if result.overlap.excluded_sleeve_ids.length > 0}
				<p class="faint small">
					{result.overlap.excluded_sleeve_ids.length} multi-product sleeve{result.overlap
						.excluded_sleeve_ids.length === 1
						? ' is'
						: 's are'} excluded from overlap.
				</p>
			{/if}
			<h2 class="sub">Equal-weight basket</h2>
			{#each result.basket.legs as leg (leg.product_id)}
				<div class="check">
					<span class="muted">{leg.product_id} buy &amp; hold</span>
					<span class={tone(leg.return_fraction)}>{signedPercent(leg.return_fraction)}</span>
				</div>
			{/each}
		</section>
	</div>

	<section class="card body" aria-label="How this was simulated">
		<h2>How this was simulated</h2>
		<ul class="disclosures" data-testid="backtest-disclosures">
			{#each result.disclosures as text, index (index)}
				<li>{text}</li>
			{/each}
		</ul>
	</section>
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
	<section class="card" aria-label="Earlier runs">
		<div class="card-head"><h2>Earlier runs</h2></div>
		<ul class="runs" data-testid="backtest-runs">
			{#each listings as listing (listing.result_fingerprint)}
				<li>
					<button
						type="button"
						class="run-row"
						aria-current={listing.result_fingerprint === selected ? 'true' : undefined}
						onclick={() => void select(listing.result_fingerprint)}
					>
						<span class="mono">{utcMinute(listing.published_at)}</span>
						<span class={tone(listing.total_return_fraction)}
							>{signedPercent(listing.total_return_fraction)}</span
						>
						<span class="faint">DD {drawdownPercent(listing.maximum_drawdown_fraction)}</span>
						<span class="faint"
							>{listing.sleeve_count} sleeves · rev {listing.portfolio_revision}</span
						>
					</button>
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.card {
		margin-top: 16px;
	}
	.run {
		padding: 16px 18px;
	}
	.run-head {
		display: grid;
		gap: 4px;
		margin-bottom: 12px;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
		align-items: end;
		gap: 10px;
	}
	.field {
		display: grid;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.field input {
		min-height: 34px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
	}
	.run-foot {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		margin-top: 10px;
	}
	.progress {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		margin-top: 12px;
		padding: 10px 12px;
		border: 1px solid var(--accent-line);
		border-radius: var(--radius-md);
		background: var(--accent-soft);
	}
	.progress.failed {
		border-color: var(--danger-line);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.pulse {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--accent);
	}
	@media (prefers-reduced-motion: no-preference) {
		.pulse {
			animation: pulse 1.2s ease-in-out infinite;
		}
	}
	@keyframes pulse {
		50% {
			opacity: 0.3;
		}
	}
	.problem-box {
		margin-top: 12px;
		padding: 10px 12px;
		border: 1px solid var(--danger-line);
		border-radius: var(--radius-md);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.problem-box p {
		margin: 0;
	}
	.problem-box ul {
		margin: 8px 0 0;
		padding-left: 18px;
		color: var(--text);
	}
	.card-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head .chip {
		margin-left: auto;
	}
	.result .metrics,
	.result :global(.chart),
	.result > p {
		margin: 14px 16px;
	}
	.metrics {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: 14px;
	}
	.metric .l {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.metric .v {
		margin-top: 2px;
		font-size: var(--fs-xl);
		font-weight: 600;
	}
	.metric .n {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.grid2 {
		display: grid;
		grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
		gap: 16px;
	}
	.body {
		padding: 16px 18px;
	}
	.sub {
		margin-top: 16px;
	}
	.check {
		display: flex;
		gap: 10px;
		padding: 8px 0;
		border-top: 1px solid var(--line);
		font-size: var(--fs-sm);
	}
	.check > span:first-child {
		flex: 1;
	}
	.row-head {
		color: var(--muted);
		font-weight: 500;
		text-align: left;
		padding: 11px 16px;
		border-top: 1px solid var(--line);
		font-size: var(--fs-base);
	}
	.disclosures {
		margin: 10px 0 0;
		padding-left: 18px;
		color: var(--muted);
		display: grid;
		gap: 6px;
	}
	.runs {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.run-row {
		display: grid;
		grid-template-columns: 170px 90px 110px 1fr;
		gap: 12px;
		width: 100%;
		padding: 10px 16px;
		border: 0;
		border-top: 1px solid var(--line);
		background: transparent;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}
	.runs li:first-child .run-row {
		border-top: 0;
	}
	.run-row:hover {
		background: var(--hover);
	}
	.run-row[aria-current='true'] {
		background: var(--accent-soft);
	}
	.pad {
		padding: 0 16px 14px;
	}
	.pos {
		color: var(--pos);
	}
	.neg {
		color: var(--neg);
	}
	.faint {
		color: var(--faint);
	}
	.muted {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.problem {
		color: var(--neg);
	}
	.empty-state {
		margin-top: 16px;
	}
	@media (max-width: 1100px) {
		.metrics {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
		.fields {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.grid2 {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
