<script lang="ts">
	import { onMount } from 'svelte';
	import { formatUtcTimestamp } from '$lib/time';
	import {
		fetchJournals,
		fetchMemoryMonitor,
		fetchMemoryStatus,
		fetchNotifications,
		fetchPatterns,
		fetchSentiment,
		type JournalEntry,
		type MemoryStatus,
		type MonitorFinding,
		type NotificationRecord,
		type PatternObservation,
		type SentimentSnapshot
	} from '$lib/memory';

	let status: MemoryStatus | null = $state(null);
	let journals: JournalEntry[] = $state([]);
	let sentiment: SentimentSnapshot[] = $state([]);
	let patterns: PatternObservation[] = $state([]);
	let notifications: NotificationRecord[] = $state([]);
	let findings: MonitorFinding[] = $state([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function loadMemory(): Promise<void> {
		loading = true;
		error = null;
		try {
			const [nextStatus, monitor, nextJournals, nextSentiment, nextPatterns, nextNotifications] =
				await Promise.all([
					fetchMemoryStatus(),
					fetchMemoryMonitor(),
					fetchJournals(),
					fetchSentiment(),
					fetchPatterns(),
					fetchNotifications()
				]);
			status = nextStatus;
			findings = monitor.findings;
			journals = nextJournals;
			sentiment = nextSentiment;
			patterns = nextPatterns;
			notifications = nextNotifications;
		} catch (caught) {
			status = null;
			findings = [];
			journals = [];
			sentiment = [];
			patterns = [];
			notifications = [];
			error = caught instanceof Error ? caught.message : 'Experiential memory is unavailable.';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void loadMemory();
	});
</script>

<svelte:head>
	<title>Memory · ThyTrader</title>
</svelte:head>

<main>
	<section class="hero">
		<div>
			<p class="eyebrow">Experiential memory</p>
			<h1>Journals and monitor</h1>
			<p class="lede">
				Origin-attributed facts, lessons, sentiment, and pattern hooks. Mutations stay on
				<code>thytrader-memory --confirm</code>; this page is read-only. YOLO never covers that lane.
			</p>
		</div>
		<button class="refresh" type="button" onclick={loadMemory} disabled={loading}>
			<span class:spinning={loading}>↻</span>
			{loading ? 'Refreshing…' : 'Refresh memory'}
		</button>
	</section>

	{#if error}
		<div class="error-banner" role="alert">
			<div>
				<strong>Couldn't load memory</strong>
				<p>{error}</p>
			</div>
			<button type="button" onclick={loadMemory}>Try again</button>
		</div>
	{/if}

	{#if status}
		<section class="status-grid" data-testid="memory-status">
			<div>
				<p class="label">Storage</p>
				<p>{status.storage}</p>
			</div>
			<div>
				<p class="label">Notify</p>
				<p>
					{status.notify_provider}
					{status.notify_webhook_configured ? '(webhook configured)' : '(no webhook URL)'}
				</p>
			</div>
			<div>
				<p class="label">Counts</p>
				<p>
					{status.counts.journals} journals · {status.counts.sentiment} sentiment ·
					{status.counts.patterns} patterns · {status.counts.notifications} notify
				</p>
			</div>
		</section>
	{/if}

	{#if findings.length > 0}
		<section class="findings" data-testid="memory-findings">
			<h2>Monitor findings</h2>
			<ul>
				{#each findings as finding (finding.reason_code + (finding.deployment_id ?? ''))}
					<li><code>{finding.reason_code}</code> — {finding.detail}</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#if loading && journals.length === 0 && !error}
		<section class="loading-card" aria-label="Loading memory">
			<div class="skeleton wide"></div>
			<div class="skeleton"></div>
		</section>
	{:else if !error && journals.length === 0}
		<section class="empty-state">
			<h3>No journal rows yet</h3>
			<p>Append facts and lessons with <code>uv run thytrader-memory add-journal --confirm</code>.</p>
		</section>
	{:else if journals.length > 0}
		<section class="panel">
			<div class="panel-heading">
				<h2>Journals</h2>
			</div>
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>When (UTC)</th>
							<th>Origin</th>
							<th>Kind</th>
							<th>Title</th>
						</tr>
					</thead>
					<tbody>
						{#each journals as entry (entry.id)}
							<tr>
								<td class="timestamp">{formatUtcTimestamp(entry.occurred_at)}</td>
								<td>{entry.origin}</td>
								<td>{entry.kind}</td>
								<td>{entry.title}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}

	{#if sentiment.length > 0}
		<section class="panel">
			<h2>Sentiment hooks</h2>
			<ul>
				{#each sentiment as row (row.id)}
					<li>{row.origin} · {row.label} · {row.product_id ?? 'no product'}</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#if patterns.length > 0}
		<section class="panel">
			<h2>Pattern hooks</h2>
			<ul>
				{#each patterns as row (row.id)}
					<li>{row.origin} · {row.pattern_key} · {row.status}</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#if notifications.length > 0}
		<section class="panel">
			<h2>Notifications</h2>
			<ul>
				{#each notifications as row (row.id)}
					<li>{row.origin} · {row.delivery_status} · {row.title}</li>
				{/each}
			</ul>
		</section>
	{/if}
</main>

<style>
	.hero {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 1rem;
	}
	.eyebrow {
		font-size: 0.8125rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: #a0aec0;
		margin: 0 0 0.25rem 0;
	}
	h1 {
		font-size: 2rem;
		margin: 0 0 0.5rem 0;
		color: #f7fafc;
	}
	.lede {
		color: #a0aec0;
		margin: 0;
		max-width: 42rem;
	}
	.refresh {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		background: #2b6cb0;
		color: #fff;
		border: none;
		padding: 0.5rem 1rem;
		border-radius: 0.375rem;
		cursor: pointer;
		font-weight: 600;
	}
	.refresh:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.spinning {
		display: inline-block;
		animation: spin 1s linear infinite;
	}
	@keyframes spin {
		100% {
			transform: rotate(360deg);
		}
	}
	.error-banner {
		margin-top: 1.5rem;
		padding: 1rem;
		border: 1px solid #c53030;
		border-radius: 0.5rem;
		background: #2d1b1b;
		color: #feb2b2;
		display: flex;
		justify-content: space-between;
		gap: 1rem;
	}
	.status-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
		gap: 1rem;
		margin-top: 1.5rem;
	}
	.label {
		margin: 0;
		color: #a0aec0;
		font-size: 0.75rem;
		text-transform: uppercase;
	}
	.findings,
	.panel,
	.empty-state,
	.loading-card {
		margin-top: 1.5rem;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
	}
	th,
	td {
		text-align: left;
		padding: 0.5rem 0.75rem;
		border-bottom: 1px solid #2d3748;
	}
	.timestamp {
		white-space: nowrap;
	}
	.skeleton {
		height: 0.75rem;
		background: #2d3748;
		border-radius: 0.25rem;
		margin-bottom: 0.5rem;
	}
	.skeleton.wide {
		width: 60%;
	}
</style>
