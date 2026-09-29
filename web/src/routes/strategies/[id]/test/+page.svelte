<script lang="ts">
	/**
	 * Test stage: run backtests and composed studies against the selected
	 * exact version, list every published result for this strategy's
	 * versions, and inspect one inline (`?result=` deep link).
	 *
	 * Results are research evidence. There is deliberately no Deploy or Start
	 * paper action here: running a version starts from the Run stage, never
	 * from a backtest result.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { untrack } from 'svelte';
	import BacktestDetail from '$lib/BacktestDetail.svelte';
	import ResearchLaunchPanel from '$lib/ResearchLaunchPanel.svelte';
	import {
		compareDecimalStrings,
		fetchAllBacktestsForFingerprint,
		fetchBacktest,
		fetchBacktestBenchmark,
		fetchBacktestMetrics,
		formatPercent,
		parseResultFingerprintParam,
		type BacktestBenchmark,
		type BacktestDetail as BacktestDetailData,
		type BacktestPerformanceMetrics,
		type BacktestSummaryEntry
	} from '$lib/backtests';
	import { engineContractLabel } from '$lib/research-studies';
	import { workspaceHref } from '$lib/strategy-workspace';
	import { formatUtcTimestamp } from '$lib/time';
	import { useWorkspace } from '$lib/workspace/workspace.svelte';

	const workspace = useWorkspace();

	type ResultRow = BacktestSummaryEntry & { version: number };

	let rows = $state<ResultRow[]>([]);
	let rowsLoading = $state(false);
	let rowsError = $state<string | null>(null);
	let thisVersionOnly = $state(false);
	let rowsRequest = 0;

	let detail = $state<BacktestDetailData | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);
	let benchmark = $state<BacktestBenchmark | null>(null);
	let benchmarkLoading = $state(false);
	let benchmarkError = $state<string | null>(null);
	let metrics = $state<BacktestPerformanceMetrics | null>(null);
	let metricsLoading = $state(false);
	let metricsError = $state<string | null>(null);
	let detailRequest = 0;

	const selected = $derived(workspace.version.entry);
	const selectedModel = $derived(workspace.selectedModel);
	const resultFingerprint = $derived(
		parseResultFingerprintParam(page.url.searchParams.get('result'))
	);
	const versionsKey = $derived(
		(workspace.history?.versions ?? []).map((version) => version.strategy_fingerprint).join(',')
	);
	const visibleRows = $derived(
		thisVersionOnly && selected
			? rows.filter((row) => row.strategy_fingerprint === selected.strategy_fingerprint)
			: rows
	);
	/** A `?result=` must belong to one of this strategy's published versions. */
	const foreignResult = $derived(
		detail !== null &&
			!(workspace.history?.versions ?? []).some(
				(version) => version.strategy_fingerprint === detail?.result.strategy_fingerprint
			)
	);

	$effect(() => {
		void versionsKey;
		untrack(() => void loadRows());
	});

	$effect(() => {
		const fingerprint = resultFingerprint;
		untrack(() => {
			if (fingerprint === null) resetDetail();
			else beginDetail(fingerprint);
		});
	});

	async function loadRows(): Promise<void> {
		const requestId = ++rowsRequest;
		const versions = workspace.history?.versions ?? [];
		rowsLoading = true;
		rowsError = null;
		try {
			const groups = await Promise.all(
				versions.map(async (version) =>
					(await fetchAllBacktestsForFingerprint(version.strategy_fingerprint)).map(
						(row): ResultRow => ({ ...row, version: version.version })
					)
				)
			);
			if (requestId !== rowsRequest) return;
			rows = groups.flat().sort((a, b) => b.published_at.localeCompare(a.published_at));
		} catch (caught) {
			if (requestId !== rowsRequest) return;
			rowsError = caught instanceof Error ? caught.message : 'Backtest results are unavailable.';
		} finally {
			if (requestId === rowsRequest) rowsLoading = false;
		}
	}

	function resetDetail(): void {
		detailRequest += 1;
		detail = null;
		detailError = null;
		detailLoading = false;
		benchmark = null;
		benchmarkError = null;
		benchmarkLoading = false;
		metrics = null;
		metricsError = null;
		metricsLoading = false;
	}

	function beginDetail(fingerprint: string): void {
		const requestId = ++detailRequest;
		detail = null;
		detailError = null;
		detailLoading = true;
		benchmark = null;
		benchmarkError = null;
		benchmarkLoading = true;
		metrics = null;
		metricsError = null;
		metricsLoading = true;
		fetchBacktest(fingerprint)
			.then((value) => {
				if (requestId === detailRequest) detail = value;
			})
			.catch((caught: unknown) => {
				if (requestId === detailRequest)
					detailError =
						caught instanceof Error ? caught.message : 'Backtest result is unavailable.';
			})
			.finally(() => {
				if (requestId === detailRequest) detailLoading = false;
			});
		fetchBacktestBenchmark(fingerprint)
			.then((value) => {
				if (requestId === detailRequest) benchmark = value.benchmark;
			})
			.catch((caught: unknown) => {
				if (requestId === detailRequest)
					benchmarkError =
						caught instanceof Error ? caught.message : 'Backtest benchmark is unavailable.';
			})
			.finally(() => {
				if (requestId === detailRequest) benchmarkLoading = false;
			});
		fetchBacktestMetrics(fingerprint)
			.then((value) => {
				if (requestId === detailRequest) metrics = value.metrics;
			})
			.catch((caught: unknown) => {
				if (requestId === detailRequest)
					metricsError =
						caught instanceof Error ? caught.message : 'Backtest metrics are unavailable.';
			})
			.finally(() => {
				if (requestId === detailRequest) metricsLoading = false;
			});
	}

	function resultHref(row: ResultRow): `/strategies/${string}` {
		return workspaceHref(workspace.strategyId, 'test', {
			version: row.strategy_fingerprint,
			result: row.result_fingerprint
		});
	}

	function closeResult(): void {
		void goto(
			resolve(workspaceHref(workspace.strategyId, 'test', { version: workspace.requestedVersion })),
			{ keepFocus: true, noScroll: true }
		);
	}

	async function onBacktestLaunched(result: string): Promise<void> {
		await goto(
			resolve(
				workspaceHref(workspace.strategyId, 'test', {
					version: selected?.strategy_fingerprint ?? null,
					result
				})
			)
		);
		void loadRows();
	}
</script>

<svelte:head><title>Test · {workspace.name ?? 'Strategy'} · ThyTrader</title></svelte:head>

{#if workspace.version.status === 'none'}
	<div class="empty-state">
		<h2>No published version yet</h2>
		<p>Validate and publish this draft first. Backtests run against an immutable version.</p>
		<a class="btn" href={resolve(workspaceHref(workspace.strategyId, 'build'))}>Go to Build</a>
	</div>
{:else if selected && selectedModel}
	<ResearchLaunchPanel
		strategyId={workspace.strategyId}
		productId={selectedModel.product_id}
		model={selectedModel}
		fingerprint={selected.strategy_fingerprint}
		onBacktestLaunched={(result) => void onBacktestLaunched(result)}
	/>
{:else if selected && workspace.modelErrors[selected.strategy_fingerprint]}
	<div class="error-banner" role="alert">
		<div>
			<strong>Published definition unavailable</strong>
			<p>{workspace.modelErrors[selected.strategy_fingerprint]}</p>
		</div>
	</div>
{:else}
	<div class="loading-card" aria-busy="true"><div class="skeleton"></div></div>
{/if}

{#if resultFingerprint !== null}
	<section class="card result" data-testid="workspace-result">
		{#if foreignResult}
			<div class="problem" role="alert">
				<strong>This result does not belong to this strategy.</strong>
				<p>
					Its strategy fingerprint is not one of this strategy's published versions.
					<a href={resolve(`/backtests?result=${encodeURIComponent(resultFingerprint)}`)}
						>Open it on its own</a
					>.
				</p>
			</div>
		{:else}
			<p class="evidence-note">
				Simulated result from candle data: research evidence, not a promise. It does not start or
				qualify a runtime.
			</p>
			<BacktestDetail
				{detail}
				{benchmark}
				{benchmarkLoading}
				{benchmarkError}
				{metrics}
				{metricsLoading}
				{metricsError}
				loading={detailLoading}
				error={detailError}
				backLabel="× Close result"
				onBack={closeResult}
			/>
		{/if}
	</section>
{/if}

<section class="card results" aria-labelledby="results-title">
	<div class="card-head">
		<h2 id="results-title">Results for this strategy</h2>
		{#if selected}
			<label class="only">
				<input type="checkbox" bind:checked={thisVersionOnly} /> Only v{selected.version}
			</label>
		{/if}
		<button class="btn ghost" type="button" onclick={() => void loadRows()} disabled={rowsLoading}
			>{rowsLoading ? 'Loading…' : 'Reload'}</button
		>
	</div>
	{#if rowsLoading && rows.length === 0}
		<div class="pad"><div class="skeleton"></div></div>
	{:else if rowsError}
		<p class="pad problem" role="alert">Backtest results could not be loaded: {rowsError}</p>
	{:else if visibleRows.length === 0}
		<p class="pad muted">No backtest evidence for this version yet.</p>
	{:else}
		<div class="table-wrap">
			<table aria-label="Backtest results for this strategy">
				<thead>
					<tr>
						<th scope="col">Version</th>
						<th scope="col">Engine</th>
						<th scope="col" class="num">Net</th>
						<th scope="col" class="num">Max DD</th>
						<th scope="col" class="num">Trades</th>
						<th scope="col" class="num">Win rate</th>
						<th scope="col">Published</th>
						<th scope="col"><span class="sr-only">Open</span></th>
					</tr>
				</thead>
				<tbody>
					{#each visibleRows as row (row.result_fingerprint)}
						<tr class:current={row.result_fingerprint === resultFingerprint}>
							<td>v{row.version}</td>
							<td>{engineContractLabel(row.engine_contract_version)}</td>
							<td
								class="num"
								class:pos={compareDecimalStrings(row.summary.total_return_fraction, '0') > 0}
								class:neg={compareDecimalStrings(row.summary.total_return_fraction, '0') < 0}
								>{formatPercent(row.summary.total_return_fraction)}</td
							>
							<td class="num neg">{formatPercent(row.summary.maximum_drawdown_fraction)}</td>
							<td class="num">{row.summary.trade_count}</td>
							<td class="num">{formatPercent(row.summary.win_rate)}</td>
							<td class="muted">{formatUtcTimestamp(row.published_at)}</td>
							<td
								><a
									href={resolve(resultHref(row))}
									aria-current={row.result_fingerprint === resultFingerprint ? 'true' : undefined}
									aria-label="Inspect v{row.version} result published {formatUtcTimestamp(
										row.published_at
									)}">Inspect</a
								></td
							>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>

<style>
	.result {
		margin-bottom: var(--space-4);
		padding: 16px;
	}
	.evidence-note {
		margin: 0 0 10px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head h2 {
		margin-right: auto;
	}
	.only {
		display: flex;
		align-items: center;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.pad {
		margin: 0;
		padding: 16px;
	}
	.muted {
		color: var(--muted);
	}
	.problem {
		color: var(--neg);
	}
	.problem p {
		margin: 4px 0 0;
	}
	th,
	td {
		text-align: left;
	}
	.num {
		text-align: right;
	}
	.pos {
		color: var(--pos);
	}
	.neg {
		color: var(--neg);
	}
	tr.current td {
		background: var(--accent-soft);
	}
</style>
