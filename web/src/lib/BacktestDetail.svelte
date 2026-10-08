<script lang="ts">
	/**
	 * One saved backtest result, read-only: its header, then the panels in
	 * `$lib/backtest-detail/` (headline metrics, fee attribution, equity, modeled
	 * assumptions, entry diagnostics, fingerprints, ratio metrics, buy-and-hold
	 * comparison, trade ledger) and the per-bar explanations.
	 */
	import {
		formatEvaluationWindow,
		formatSpreadCostNote,
		type BacktestBenchmark,
		type BacktestDetail,
		type BacktestPerformanceMetrics
	} from '$lib/backtests';
	import BacktestBarExplanations from '$lib/BacktestBarExplanations.svelte';
	import BenchmarkPanel from '$lib/backtest-detail/BenchmarkPanel.svelte';
	import CostAttribution from '$lib/backtest-detail/CostAttribution.svelte';
	import EntryDiagnostics from '$lib/backtest-detail/EntryDiagnostics.svelte';
	import EquityPanel from '$lib/backtest-detail/EquityPanel.svelte';
	import HeadlineMetrics from '$lib/backtest-detail/HeadlineMetrics.svelte';
	import ModeledAssumptions from '$lib/backtest-detail/ModeledAssumptions.svelte';
	import RatioMetrics from '$lib/backtest-detail/RatioMetrics.svelte';
	import ResultFingerprints from '$lib/backtest-detail/ResultFingerprints.svelte';
	import TradeLedger from '$lib/backtest-detail/TradeLedger.svelte';
	import { formatUtcTimestamp } from '$lib/time';

	let {
		detail,
		benchmark = null,
		benchmarkLoading = false,
		benchmarkError = null,
		metrics = null,
		metricsLoading = false,
		metricsError = null,
		loading = false,
		error = null,
		backLabel = '← All backtests',
		versionLabel = null,
		publishedAt = null,
		onBack
	}: {
		detail: BacktestDetail | null;
		benchmark?: BacktestBenchmark | null;
		benchmarkLoading?: boolean;
		benchmarkError?: string | null;
		metrics?: BacktestPerformanceMetrics | null;
		metricsLoading?: boolean;
		metricsError?: string | null;
		loading?: boolean;
		error?: string | null;
		/** Label of the button that leaves this detail view. */
		backLabel?: string;
		/** `Current rules` / `Earlier edit` when the result's snapshot is known (workspace Test stage). */
		versionLabel?: string | null;
		/** When this result was saved, if the caller's list knows it. */
		publishedAt?: string | null;
		onBack: () => void;
	} = $props();
	const result = $derived(detail?.result ?? null);
	/** `2026-08-01 → 2026-09-29 · 1,400 bars`, read from the result's own equity curve. */
	const periodText = $derived.by((): string => {
		if (result === null) return '';
		const bars = `${result.summary.evaluation_bars} bars`;
		const curve = result.equity_curve;
		if (curve.length < 2) return bars;
		return `${curve[0].candle_starts_at.slice(0, 10)} → ${curve[curve.length - 1].candle_starts_at.slice(0, 10)} · ${bars}`;
	});
	const buyAndHold = $derived(
		benchmark?.total_return_fraction ?? metrics?.buy_and_hold_return_fraction ?? null
	);
	const spreadCostNote = $derived(
		result ? formatSpreadCostNote(detail?.costs, result.summary.total_spread_cost) : null
	);
	/** Spread columns and notes appear only on spread-stressed runs. */
	const spreadStressed = $derived(spreadCostNote !== null);
	const validityLimits = $derived(result?.summary.validity_limits ?? []);
	const diagnostics = $derived(detail?.diagnostics ?? null);
	const evaluationWindow = $derived(detail?.window ?? null);
	const attribution = $derived(detail?.cost_attribution ?? null);
</script>

<section class="detail" aria-label="Backtest result detail">
	<div class="result-head" data-testid="backtest-result-head">
		<div class="result-id">
			<h2>
				{#if result}{versionLabel ? `${versionLabel} · ` : ''}{periodText}{:else}Backtest result{/if}
			</h2>
			{#if publishedAt}<span class="faint">{formatUtcTimestamp(publishedAt)}</span>{/if}
			<span class="chip">Simulated result (candle-based fills)</span>
		</div>
		<button type="button" class="back" onclick={onBack}>{backLabel}</button>
	</div>
	<p class="evidence-only">
		Historical evidence only · saved backtest result of a strategy snapshot · this page cannot
		submit orders or regenerate the run.
	</p>
	{#if loading}<div class="empty"><div class="skeleton"></div></div>
	{:else if error}<div class="empty">
			<p>Backtest result could not be loaded.</p>
			<small>{error}</small>
		</div>
	{:else if result && detail}
		<HeadlineMetrics {result} {buyAndHold} {benchmarkLoading} />
		<CostAttribution {attribution} />
		{#if evaluationWindow}
			<p class="evaluation-window" data-testid="result-window">
				<strong>{formatEvaluationWindow(evaluationWindow)}</strong>
				<span class="faint"
					>Omitted bounds start after each strategy's own warmup, so strategies with different
					warmups cover different bars. Pin <code>evaluation_start</code> and
					<code>evaluation_end</code> when you compare strategies.</span
				>
			</p>
		{/if}
		<EquityPanel {result} />
		<ModeledAssumptions costs={detail.costs} {spreadCostNote} {validityLimits} />
		<EntryDiagnostics {diagnostics} tradeCount={result.summary.trade_count} />
		<ResultFingerprints resultFingerprint={detail.result_fingerprint} {result} />
		<RatioMetrics {metrics} {metricsLoading} {metricsError} />
		<BenchmarkPanel {result} {benchmark} {benchmarkLoading} {benchmarkError} />
		<TradeLedger {result} {spreadStressed} />
		<BacktestBarExplanations resultFingerprint={detail.result_fingerprint} />
	{/if}
</section>

<style>
	.detail {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 16px;
		overflow-wrap: anywhere;
	}
	.result-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px 16px;
	}
	.result-id {
		display: flex;
		flex: 1;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px 12px;
		min-width: 0;
	}
	.result-id h2 {
		margin: 0;
		font-size: var(--fs-md);
	}
	.result-id .chip {
		margin-left: auto;
	}
	.back {
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--accent);
		cursor: pointer;
		font-size: 13px;
	}
	.evidence-only {
		margin-top: -8px;
	}
	.faint {
		color: var(--faint);
	}
	p {
		margin: 0;
		color: var(--faint);
		font-size: 12px;
	}
	code {
		color: var(--code);
	}
	small {
		color: var(--faint);
		font-size: 11px;
	}
	.evaluation-window {
		margin: 0;
		padding: 10px 18px;
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.5;
	}
	.evaluation-window strong {
		color: var(--text);
	}
	.empty {
		padding: 32px;
		text-align: center;
	}
	.empty p {
		margin: 0 0 6px;
		color: var(--muted);
		font-size: 14px;
	}
	.empty small {
		color: var(--faint);
		font-size: 12px;
	}
	.skeleton {
		height: 60px;
		border-radius: 8px;
		background: linear-gradient(90deg, var(--surface-2), var(--hover), var(--surface-2));
		background-size: 200%;
		animation: shimmer 1.4s infinite;
	}
	@keyframes shimmer {
		to {
			background-position: -200% 0;
		}
	}
</style>
