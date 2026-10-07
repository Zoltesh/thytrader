<script lang="ts">
	/** A read-only snapshot over all enabled watches, distinct from historical coverage. */
	import { onMount } from 'svelte';
	import { fetchDataHealth, tailDescription, type DataHealthReport } from './data-health';
	import { formatShortUtc } from './home-format';

	let report = $state<DataHealthReport | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	async function refresh(): Promise<void> {
		loading = true;
		error = null;
		try {
			report = await fetchDataHealth();
		} catch (caught) {
			report = null;
			error = caught instanceof Error ? caught.message : 'Freshness unavailable.';
		} finally {
			loading = false;
		}
	}
	onMount(() => {
		void refresh();
	});
</script>

<section aria-labelledby="watched-tails-title" data-testid="watched-tails">
	<h3 id="watched-tails-title">Watched-market freshness</h3>
	<p>
		Each tail is compared with its own latest closed candle. Historical coverage and worker success
		do not prove freshness.
	</p>
	<button type="button" class="btn ghost" onclick={() => void refresh()} disabled={loading}>
		{loading ? 'Reading…' : 'Refresh freshness'}
	</button>
	{#if error}
		<p role="status">{error}</p>
	{:else if report && !loading}
		<p>
			Snapshot {formatShortUtc(report.generated_at)} · {report.payload.attention_count} of {report
				.payload.watched_count} watched tails need attention.
		</p>
		{#if !report.payload.inventory_complete}
			<p role="status">
				Incomplete inventory — unavailable series are not healthy. {report.partial_result_warnings.join(
					' '
				)}
			</p>
		{/if}
		{#if report.payload.datasets.length === 0}
			<p>No enabled watched series returned.</p>
		{:else}
			<div class="table-wrap">
				<table>
					<thead
						><tr
							><th>Dataset</th><th>Tail</th><th>Expected close</th><th>Published through</th><th
								>History</th
							></tr
						></thead
					>
					<tbody>
						{#each report.payload.datasets as row (`${row.provider}|${row.product_id}|${row.timeframe}`)}
							<tr>
								<td>{row.product_id} · {row.timeframe}</td>
								<td>{tailDescription(row)}</td>
								<td>{formatShortUtc(row.expected_closed_end)}</td>
								<td>{row.covered_ends_at ? formatShortUtc(row.covered_ends_at) : 'Unknown'}</td>
								<td
									>{row.watch_complete === true
										? 'Watch covered'
										: row.watch_complete === false
											? 'Incomplete'
											: 'Unknown'}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{/if}
</section>

<style>
	section {
		margin-top: 1rem;
	}
	p {
		font-size: 0.875rem;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
	}
	th,
	td {
		padding: 0.5rem;
		font-size: 0.8125rem;
	}
</style>
