<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { formatUtcTimestamp } from '$lib/time';
	import { fetchJournals, type JournalEntry } from '$lib/memory';

	let journals: JournalEntry[] = $state([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function loadJournals(): Promise<void> {
		loading = true;
		error = null;
		try {
			journals = await fetchJournals();
		} catch (caught) {
			journals = [];
			error = caught instanceof Error ? caught.message : 'Journals are unavailable.';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void loadJournals();
	});
</script>

<svelte:head>
	<title>Journals · ThyTrader</title>
</svelte:head>

<main>
	<section class="hero">
		<div>
			<p class="eyebrow">Experiential memory</p>
			<h1>Journals</h1>
			<p class="lede">
				Origin-attributed facts, lessons, and notes from experiential memory. Why-trade review stays
				on Memory and Trade. Mutations stay on
				<code>thytrader-memory --confirm</code>. YOLO never covers that lane.
			</p>
		</div>
		<button class="refresh" type="button" onclick={() => void loadJournals()} disabled={loading}>
			<span class:spinning={loading}>↻</span>
			Refresh
		</button>
	</section>
	<p class="destination-note">
		Per-intent why-trade records live on
		<a href={resolve('/memory')}>Memory</a>
		and
		<a href={resolve('/trade')}>Trade</a>. This page lists experiential journal rows only.
	</p>
	{#if error}
		<div class="error-banner" role="alert">{error}</div>
	{:else if loading && journals.length === 0}
		<p class="empty-hint">Loading journals…</p>
	{:else if journals.length === 0}
		<p class="empty-hint">
			No journal rows yet. Append with <code>uv run thytrader-memory add-journal --confirm</code>.
		</p>
	{:else}
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>When (UTC)</th>
						<th>Origin</th>
						<th>Kind</th>
						<th>Title</th>
						<th>Product</th>
					</tr>
				</thead>
				<tbody>
					{#each journals as entry (entry.id)}
						<tr>
							<td class="timestamp">{formatUtcTimestamp(entry.occurred_at)}</td>
							<td>{entry.origin}</td>
							<td>{entry.kind}</td>
							<td>{entry.title}</td>
							<td>{entry.product_id ?? '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</main>

<style>
	.destination-note {
		color: #8d999c;
		font-size: 14px;
		margin: 0 0 24px;
		max-width: 72ch;
	}
	.destination-note a {
		color: #7fd0f0;
	}
	.empty-hint,
	.error-banner {
		color: #8d999c;
	}
	.error-banner {
		color: #f0a3a3;
	}
	.table-wrap {
		overflow-x: auto;
		border: 1px solid #303a3c;
		border-radius: 12px;
		background: #141b1c;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}
	th,
	td {
		text-align: left;
		padding: 12px 16px;
		border-bottom: 1px solid #232d2e;
	}
	th {
		color: #aeb9bb;
		font-weight: 500;
		font-size: 12px;
	}
	.timestamp {
		font-variant-numeric: tabular-nums;
		color: #aeb9bb;
	}
</style>
