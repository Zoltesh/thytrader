<script lang="ts">
	import { formatUtcTimestamp } from '$lib/time';
	import type { TradeReasonRecord } from '$lib/memory';

	let {
		records = [] as TradeReasonRecord[],
		emptyMessage = 'No why-trade records yet.'
	}: {
		records?: TradeReasonRecord[];
		emptyMessage?: string;
	} = $props();

	function strategyLabel(record: TradeReasonRecord): string {
		if (record.strategy === null || record.strategy.name === null) {
			return 'discretionary';
		}
		return record.strategy.name;
	}

	function notesLabel(record: TradeReasonRecord): string {
		if (record.notes.length === 0) {
			return 'none';
		}
		return record.notes.map((note) => `${note.origin}: ${note.body}`).join(' · ');
	}

	function reconcileLabel(record: TradeReasonRecord): string {
		const { reconcile } = record;
		if (!reconcile.ledger_available) {
			return 'ledger unavailable';
		}
		if (reconcile.unknown_timeout) {
			return 'unknown timeout';
		}
		if (reconcile.order_status === null) {
			return 'intent only';
		}
		const fills = reconcile.fills.length;
		return `${reconcile.order_status} · ${fills} fill${fills === 1 ? '' : 's'}`;
	}
</script>

<section class="panel" data-testid="trade-reason-review">
	<div class="panel-heading">
		<h2>Why trades were made</h2>
		<p>
			Same <code>thytrader-trade-reason-v1</code> payload as operator reports. This panel is
			read-only; notes use <code>thytrader-memory add-trade-reason-note --confirm</code>.
		</p>
	</div>
	{#if records.length === 0}
		<p class="empty">{emptyMessage}</p>
	{:else}
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>When (UTC)</th>
						<th>Origin</th>
						<th>Product</th>
						<th>Signal</th>
						<th>Risk</th>
						<th>Strategy</th>
						<th>Reconcile</th>
						<th>Notes</th>
					</tr>
				</thead>
				<tbody>
					{#each records as record (record.id)}
						<tr>
							<td class="timestamp">{formatUtcTimestamp(record.created_at)}</td>
							<td>{record.origin}</td>
							<td>{record.product_id} · {record.mode}</td>
							<td>{record.signal.kind}</td>
							<td>{record.risk.decision} · {record.risk.reason_code}</td>
							<td>{strategyLabel(record)}</td>
							<td>{reconcileLabel(record)}</td>
							<td>{notesLabel(record)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>

<style>
	.panel {
		margin-top: 1.5rem;
	}
	/* Local layout: the global .panel-heading is a split header row. */
	.panel-heading {
		display: block;
		padding: 0;
		border: 0;
	}
	.panel-heading h2 {
		margin: 0 0 0.35rem 0;
		color: var(--text);
	}
	.panel-heading p {
		margin: 0 0 0.75rem 0;
		color: var(--muted);
		font-size: 0.875rem;
	}
	.empty {
		color: var(--muted);
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
		border-bottom: 1px solid var(--line-2);
		color: var(--text);
		font-size: 0.875rem;
		vertical-align: top;
	}
	th {
		color: var(--muted);
		font-weight: 600;
	}
	.timestamp {
		white-space: nowrap;
	}
</style>
