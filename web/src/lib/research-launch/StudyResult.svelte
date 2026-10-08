<script lang="ts">
	/**
	 * A finished research study: its aggregate and stitched OOS claim, the
	 * windows with each row's axis values, and per-candidate OOS sums from the
	 * persisted summary (ADR 0094). Rows link to their saved backtest results.
	 */
	import { resolve } from '$app/paths';
	import { formatPercent } from '$lib/backtests';
	import {
		formatAxisValues,
		formatWindowBounds,
		type ResearchStudy,
		type ResearchStudySummary
	} from '$lib/research-studies';

	let {
		studyResult,
		studySummary
	}: {
		studyResult: ResearchStudy;
		/** The persisted summary: axis values per row and per-candidate OOS sums (ADR 0094). */
		studySummary: ResearchStudySummary | null;
	} = $props();

	const axisValuesByResult = $derived(
		new Map((studySummary?.window_pnl ?? []).map((row) => [row.result_fingerprint, row]))
	);
	const studyCandidates = $derived(studySummary?.candidates ?? []);
</script>

<div class="view-block" data-testid="research-study-result">
	<h3>Research study</h3>
	<p class="view-note">
		{studyResult.kind} ·
		{studyResult.aggregate.oos_window_count} OOS window(s) · fingerprint
		{studyResult.study_fingerprint.slice(0, 18)}…
	</p>
	{#if studyResult.aggregate.mean_oos_return_fraction !== null}
		<p>
			Mean OOS return {formatPercent(studyResult.aggregate.mean_oos_return_fraction)}
			{#if studyResult.aggregate.is_oos_return_gap !== null}
				· IS−OOS gap {formatPercent(studyResult.aggregate.is_oos_return_gap)}
			{/if}
		</p>
	{/if}
	{#if studyResult.stitched_oos_equity}
		<p class="view-note">
			{#if studyResult.stitched_oos_equity.available && studyResult.stitched_oos_equity.total_return_fraction}
				Stitched OOS return {formatPercent(studyResult.stitched_oos_equity.total_return_fraction)}
				{#if studyResult.stitched_oos_equity.maximum_drawdown_fraction}
					· max drawdown {formatPercent(studyResult.stitched_oos_equity.maximum_drawdown_fraction)}
				{/if}
			{:else if studyResult.stitched_oos_equity.reason}
				{studyResult.stitched_oos_equity.reason}
			{/if}
		</p>
	{/if}
	{#each studyResult.warnings as warning, index (index)}
		<p class="view-note">{warning}</p>
	{/each}
	<table class="results-table" aria-label="Study windows">
		<thead>
			<tr>
				<th scope="col">Window</th>
				<th scope="col">Role</th>
				<th scope="col">Axis values</th>
				<th scope="col">Bounds (UTC)</th>
				<th scope="col">Return</th>
				<th scope="col">Trades</th>
			</tr>
		</thead>
		<tbody>
			{#each studyResult.windows as window (window.result_fingerprint)}
				<tr>
					<td>{window.label}</td>
					<td>{window.role}{window.selected === true ? ' · selected' : ''}</td>
					<td data-testid="study-window-axis-values"
						>{formatAxisValues(axisValuesByResult.get(window.result_fingerprint)?.axis_values)}</td
					>
					<td>{formatWindowBounds(window.evaluation_start, window.evaluation_end)}</td>
					<td>
						<a href={resolve(`/backtests?result=${encodeURIComponent(window.result_fingerprint)}`)}
							>{formatPercent(window.summary.total_return_fraction)}</a
						>
					</td>
					<td>{window.summary.trade_count}</td>
				</tr>
			{/each}
		</tbody>
	</table>
	{#if studyCandidates.length > 1}
		<table
			class="results-table"
			aria-label="Study candidates"
			data-testid="research-study-candidates"
		>
			<thead>
				<tr>
					<th scope="col">Axis values</th>
					<th scope="col">OOS net PnL (sum)</th>
					<th scope="col">OOS windows &gt; 0</th>
					<th scope="col">Selected windows</th>
				</tr>
			</thead>
			<tbody>
				{#each studyCandidates as candidate (candidate.strategy_fingerprint)}
					<tr>
						<td>{formatAxisValues(candidate.axis_values)}</td>
						<td
							>{candidate.oos_total_net_pnl ??
								candidate.full_window_total_net_pnl ??
								'—'}{candidate.oos_window_count === 0 && candidate.full_window_count > 0
								? ' (full window, not OOS)'
								: ''}</td
						>
						<td
							>{candidate.oos_window_count === 0
								? '—'
								: `${candidate.oos_positive_window_count} / ${candidate.oos_window_count}`}</td
						>
						<td>{candidate.selected_window_count}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>

<style>
	.view-block {
		display: grid;
		gap: 12px;
		margin-bottom: 28px;
	}
	.view-block h3 {
		margin: 0 0 6px;
		font-size: 12px;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.view-block p {
		margin: 0;
		font-size: 13px;
		color: var(--text);
	}
	.view-note {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.results-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 12px;
	}
	.results-table th,
	.results-table td {
		text-align: left;
		padding: 5px 8px 5px 0;
		border-bottom: 1px solid var(--line);
	}
	.results-table th {
		color: var(--muted);
		font-weight: 500;
		font-size: 11px;
	}
	.results-table tr:last-child td {
		border-bottom: none;
	}
	.results-table td a {
		color: var(--info);
	}
</style>
