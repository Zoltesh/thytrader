<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { fetchAuditEvents, type AuditEventItem } from '$lib/audit';

	let events: AuditEventItem[] = $state([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	function formatUtcTimestamp(value: string): string {
		/** Render an audit instant explicitly and deterministically in UTC. */
		return `${new Date(value).toISOString().slice(0, 19).replace('T', ' ')} UTC`;
	}

	async function loadEvents(): Promise<void> {
		loading = true;
		error = null;
		try {
			events = await fetchAuditEvents(50);
		} catch (caught) {
			events = [];
			error = caught instanceof Error ? caught.message : 'Audit event storage is unavailable.';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void loadEvents();
	});
</script>

<svelte:head>
	<title>Audit trail · ThyTrader</title>
</svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home">
			<span class="brand-mark">T</span>
			<span>ThyTrader</span>
		</a>
		<nav aria-label="Primary navigation">
			<a href={resolve('/')}>Portfolio</a>
			<a href={resolve('/strategies')}>Strategies</a>
			<a href={resolve('/backtests')}>Backtests</a>
			<a class="active" href={resolve('/audit')}>Audit</a>
		</nav>
		<div class="local-pill"><span></span> Local workstation</div>
	</header>

	<main>
		<section class="hero">
			<div>
				<p class="eyebrow">Operational audit log</p>
				<h1>Audit trail</h1>
				<p class="lede">
					Append-only operational history of worker snapshots, connections, and health transitions.
				</p>
			</div>
			<button class="refresh" type="button" onclick={loadEvents} disabled={loading}>
				<span class:spinning={loading}>↻</span>
				{loading ? 'Refreshing…' : 'Refresh audit log'}
			</button>
		</section>

		{#if error}
			<div class="error-banner" role="alert">
				<div>
					<strong>Couldn't load audit events</strong>
					<p>{error}</p>
				</div>
				<button type="button" onclick={loadEvents}>Try again</button>
			</div>
		{/if}

		{#if loading && events.length === 0}
			<section class="loading-card" aria-label="Loading audit events">
				<div class="skeleton wide"></div>
				<div class="skeleton"></div>
				<div class="skeleton"></div>
			</section>
		{:else if !error && events.length === 0}
			<section class="empty-state">
				<h3>No audit events recorded</h3>
				<p>
					Operational audit events will appear here once the worker or API registers connection and
					snapshot activities.
				</p>
			</section>
		{:else if events.length > 0}
			<section class="audit-panel">
				<div class="panel-heading">
					<div>
						<h2>Recent Events</h2>
						<p>{events.length} observations (newest first)</p>
					</div>
				</div>
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Timestamp (UTC)</th>
								<th>Category</th>
								<th>Action</th>
								<th>Outcome</th>
								<th>Provider / Product</th>
								<th>Detail</th>
							</tr>
						</thead>
						<tbody>
							{#each events as event (event.id)}
								<tr>
									<td class="timestamp">{formatUtcTimestamp(event.occurred_at)}</td>
									<td><span class="badge category">{event.category}</span></td>
									<td><code>{event.action}</code></td>
									<td><span class="badge outcome {event.outcome}">{event.outcome}</span></td>
									<td>{event.provider ?? '-'}{event.product_id ? ` / ${event.product_id}` : ''}</td>
									<td class="detail-cell">{event.detail || '-'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}
	</main>
</div>

<style>
	.shell {
		max-width: 1200px;
		margin: 0 auto;
		padding: 1.5rem;
		display: flex;
		flex-direction: column;
		gap: 2rem;
	}
	.topbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		border-bottom: 1px solid var(--border-color, #2d3748);
		padding-bottom: 1rem;
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		text-decoration: none;
		color: var(--text-color, #f7fafc);
		font-weight: 700;
		font-size: 1.25rem;
	}
	.brand-mark {
		background: #3182ce;
		color: #fff;
		width: 2rem;
		height: 2rem;
		display: flex;
		align-items: center;
		justify-content: center;
		border-radius: 0.375rem;
		font-weight: 800;
	}
	nav {
		display: flex;
		gap: 1.5rem;
	}
	nav a {
		text-decoration: none;
		color: #a0aec0;
		font-weight: 500;
		transition: color 0.15s;
	}
	nav a:hover,
	nav a.active {
		color: #63b3ed;
	}
	.local-pill {
		font-size: 0.8125rem;
		color: #a0aec0;
		display: flex;
		align-items: center;
		gap: 0.375rem;
		background: #1a202c;
		padding: 0.25rem 0.625rem;
		border-radius: 9999px;
		border: 1px solid #2d3748;
	}
	.local-pill span {
		width: 0.5rem;
		height: 0.5rem;
		background: #48bb78;
		border-radius: 50%;
	}
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
		background: #742a2a;
		border: 1px solid #e53e3e;
		color: #fff;
		padding: 1rem;
		border-radius: 0.375rem;
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.error-banner strong {
		display: block;
	}
	.error-banner p {
		margin: 0.25rem 0 0 0;
		font-size: 0.875rem;
	}
	.error-banner button {
		background: #fff;
		color: #742a2a;
		border: none;
		padding: 0.375rem 0.75rem;
		border-radius: 0.25rem;
		font-weight: 600;
		cursor: pointer;
	}
	.loading-card,
	.empty-state {
		background: #1a202c;
		border: 1px solid #2d3748;
		border-radius: 0.5rem;
		padding: 2rem;
		text-align: center;
	}
	.empty-state h3 {
		margin: 0 0 0.5rem 0;
		color: #f7fafc;
	}
	.empty-state p {
		margin: 0;
		color: #a0aec0;
	}
	.skeleton {
		height: 1.5rem;
		background: #2d3748;
		margin-bottom: 0.75rem;
		border-radius: 0.25rem;
	}
	.skeleton.wide {
		width: 60%;
		margin: 0 auto 1rem auto;
	}
	.audit-panel {
		background: #1a202c;
		border: 1px solid #2d3748;
		border-radius: 0.5rem;
		overflow: hidden;
	}
	.panel-heading {
		padding: 1rem 1.5rem;
		border-bottom: 1px solid #2d3748;
	}
	.panel-heading h2 {
		margin: 0;
		font-size: 1.25rem;
		color: #f7fafc;
	}
	.panel-heading p {
		margin: 0.25rem 0 0 0;
		font-size: 0.875rem;
		color: #a0aec0;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
		font-size: 0.875rem;
	}
	th {
		background: #2d3748;
		color: #e2e8f0;
		padding: 0.75rem 1rem;
		font-weight: 600;
	}
	td {
		padding: 0.75rem 1rem;
		border-bottom: 1px solid #2d3748;
		color: #e2e8f0;
	}
	tr:last-child td {
		border-bottom: none;
	}
	.badge {
		display: inline-block;
		padding: 0.125rem 0.375rem;
		border-radius: 0.25rem;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
	}
	.badge.category {
		background: #2b6cb0;
		color: #ebf8ff;
	}
	.badge.outcome.success {
		background: #22543d;
		color: #9ae6b4;
	}
	.badge.outcome.failure {
		background: #742a2a;
		color: #feb2b2;
	}
	.badge.outcome.info {
		background: #4a5568;
		color: #e2e8f0;
	}
	.detail-cell {
		max-width: 350px;
		word-break: break-word;
	}
	code {
		font-family: monospace;
		background: #2d3748;
		padding: 0.125rem 0.25rem;
		border-radius: 0.25rem;
		font-size: 0.8125rem;
	}
</style>
