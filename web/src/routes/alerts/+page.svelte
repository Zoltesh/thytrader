<script lang="ts">
	import PageHead from '$lib/PageHead.svelte';
	import {
		deliveryDisabledWarning,
		fetchOperatorAlerts,
		type AlertItem,
		type AlertsPayload
	} from '$lib/alerts';
	import { formatUtcTimestamp } from '$lib/time';
	import { onMount } from 'svelte';

	let payload: AlertsPayload | null = $state(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	const warning = $derived(payload ? deliveryDisabledWarning(payload) : null);

	async function loadAlerts(): Promise<void> {
		loading = true;
		error = null;
		try {
			const report = await fetchOperatorAlerts();
			payload = report.payload;
		} catch (caught) {
			payload = null;
			error = caught instanceof Error ? caught.message : 'Safety alert feed is unavailable.';
		} finally {
			loading = false;
		}
	}

	function subjectLabel(alert: AlertItem): string {
		const product = alert.product_id ? ` · ${alert.product_id}` : '';
		return `${alert.subject}${product}`;
	}

	onMount(() => {
		void loadAlerts();
	});
</script>

<svelte:head>
	<title>Safety alerts · ThyTrader</title>
</svelte:head>

<main>
	<PageHead eyebrow="Safety supervision" title="Alerts">
		{#snippet intro()}
			<p class="lede">
				Durable pause, breaker, stop-cover, deadline, and worker-failure alerts. This page does not
				place, cancel, or escalate orders. A triggered stop that is still unfilled is reported only.
			</p>
		{/snippet}
		<button class="refresh" type="button" onclick={loadAlerts} disabled={loading}>
			{loading ? 'Refreshing…' : 'Refresh alerts'}
		</button>
	</PageHead>

	{#if warning}
		<div class="warn-banner" role="status" data-testid="delivery-disabled">
			<strong>External delivery is off</strong>
			<p>{warning}</p>
		</div>
	{/if}

	{#if error}
		<div class="error-banner" role="alert">
			<strong>Couldn't load safety alerts</strong>
			<p>{error}</p>
			<button type="button" onclick={loadAlerts}>Try again</button>
		</div>
	{:else if loading && payload === null}
		<section class="panel" aria-label="Loading alerts">
			<p>Loading the local alert feed…</p>
		</section>
	{:else if payload && payload.storage === 'unavailable'}
		<section class="panel">
			<h2>Alert storage is unavailable</h2>
			<p>Durable alerts require PostgreSQL. Nothing here is invented.</p>
		</section>
	{:else if payload}
		<section class="panel">
			<div class="panel-heading">
				<h2>Open ({payload.open_total})</h2>
				<p data-testid="open-alert-count">
					{payload.open_critical} critical · {payload.open_warning} warning
				</p>
			</div>
			{#if payload.open_alerts.length === 0}
				<p class="empty">No open safety alerts.</p>
			{:else}
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Severity</th>
								<th>Code</th>
								<th>Subject</th>
								<th>Seen</th>
								<th>Delivery</th>
								<th>Detail</th>
							</tr>
						</thead>
						<tbody>
							{#each payload.open_alerts as alert (alert.id)}
								<tr>
									<td><span class="badge {alert.severity}">{alert.severity}</span></td>
									<td><code>{alert.code}</code></td>
									<td>{subjectLabel(alert)}</td>
									<td>
										{alert.occurrences}× since {formatUtcTimestamp(alert.first_seen_at)}
									</td>
									<td>{alert.delivery.status}</td>
									<td>{alert.detail}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>

		{#if payload.resolved_alerts.length > 0}
			<section class="panel">
				<div class="panel-heading">
					<h2>Recently resolved</h2>
					<p>Recovery closes the open row. A later recurrence opens a new row.</p>
				</div>
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Code</th>
								<th>Subject</th>
								<th>Resolved</th>
								<th>Occurrences</th>
							</tr>
						</thead>
						<tbody>
							{#each payload.resolved_alerts as alert (alert.id)}
								<tr>
									<td><code>{alert.code}</code></td>
									<td>{subjectLabel(alert)}</td>
									<td>{alert.resolved_at ? formatUtcTimestamp(alert.resolved_at) : '—'}</td>
									<td>{alert.occurrences}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}
	{/if}
</main>

<style>
	.warn-banner,
	.error-banner,
	.panel {
		background: var(--surface);
		border: 1px solid var(--line);
		border-radius: 0.5rem;
		padding: 1rem 1.25rem;
		margin-bottom: 1rem;
	}
	.warn-banner {
		border-color: var(--warn-line, var(--line));
	}
	.error-banner {
		background: var(--danger-soft);
		border-color: var(--danger-line);
	}
	.panel-heading h2,
	.panel h2 {
		margin: 0;
		font-size: 1.1rem;
	}
	.panel-heading p,
	.empty,
	.lede {
		color: var(--muted);
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.875rem;
	}
	th,
	td {
		text-align: left;
		padding: 0.6rem 0.75rem;
		border-bottom: 1px solid var(--line-2);
		vertical-align: top;
	}
	.badge {
		display: inline-block;
		padding: 0.1rem 0.4rem;
		border-radius: 0.25rem;
		font-size: 0.75rem;
		text-transform: uppercase;
	}
	.badge.critical {
		background: var(--danger-soft);
	}
	.badge.warning {
		background: var(--surface-2);
	}
</style>
