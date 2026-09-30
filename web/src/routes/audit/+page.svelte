<script lang="ts">
	import PageHead from '$lib/PageHead.svelte';
	import { onMount } from 'svelte';
	import { AUDIT_EVENT_LIST_LIMIT, fetchAuditEvents, type AuditEventItem } from '$lib/audit';
	import { formatUtcTimestamp } from '$lib/time';

	let events: AuditEventItem[] = $state([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function loadEvents(): Promise<void> {
		loading = true;
		error = null;
		try {
			events = await fetchAuditEvents(AUDIT_EVENT_LIST_LIMIT);
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

<main>
	<PageHead eyebrow="Operational audit log" title="Audit trail">
		{#snippet intro()}
			<p class="lede">
				Append-only operational history of worker snapshots, connections, and health transitions.
				This page shows the latest {AUDIT_EVENT_LIST_LIMIT} events; it is not the complete trail.
			</p>
		{/snippet}
		<button class="refresh" type="button" onclick={loadEvents} disabled={loading}>
			<span class:spinning={loading}>↻</span>
			{loading ? 'Refreshing…' : 'Refresh audit log'}
		</button>
	</PageHead>

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
					<h2>Latest {AUDIT_EVENT_LIST_LIMIT}</h2>
					<p data-testid="audit-list-bound">
						Showing {events.length} (latest {AUDIT_EVENT_LIST_LIMIT}, newest first)
					</p>
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

<style>
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
		background: var(--danger-soft);
		border: 1px solid var(--danger-line);
		color: var(--text);
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
		background: transparent;
		color: var(--neg);
		border: none;
		padding: 0.375rem 0.75rem;
		border-radius: 0.25rem;
		font-weight: 600;
		cursor: pointer;
	}
	.loading-card,
	.empty-state {
		background: var(--surface);
		border: 1px solid var(--line);
		border-radius: 0.5rem;
		padding: 2rem;
		text-align: center;
	}
	.empty-state h3 {
		margin: 0 0 0.5rem 0;
		color: var(--text);
	}
	.empty-state p {
		margin: 0;
		color: var(--muted);
	}
	.skeleton {
		height: 1.5rem;
		background: var(--surface-2);
		margin-bottom: 0.75rem;
		border-radius: 0.25rem;
	}
	.skeleton.wide {
		width: 60%;
		margin: 0 auto 1rem auto;
	}
	.audit-panel {
		background: var(--surface);
		border: 1px solid var(--line);
		border-radius: 0.5rem;
		overflow: hidden;
	}
	.panel-heading {
		padding: 1rem 1.5rem;
		border-bottom: 1px solid var(--line-2);
	}
	.panel-heading h2 {
		margin: 0;
		font-size: 1.25rem;
		color: var(--text);
	}
	.panel-heading p {
		margin: 0.25rem 0 0 0;
		font-size: 0.875rem;
		color: var(--muted);
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
		background: var(--surface-2);
		color: var(--text);
		padding: 0.75rem 1rem;
		font-weight: 600;
	}
	td {
		padding: 0.75rem 1rem;
		border-bottom: 1px solid var(--line-2);
		color: var(--text);
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
		background: var(--info-soft);
		color: var(--info);
	}
	.badge.outcome.success {
		background: var(--accent-soft);
		color: var(--pos);
	}
	.badge.outcome.failure {
		background: var(--danger-soft);
		color: var(--neg);
	}
	.badge.outcome.info {
		background: var(--surface-2);
		color: var(--text);
	}
	.detail-cell {
		max-width: 350px;
		word-break: break-word;
	}
	code {
		font-family: monospace;
		background: var(--surface-2);
		padding: 0.125rem 0.25rem;
		border-radius: 0.25rem;
		font-size: 0.8125rem;
	}
</style>
