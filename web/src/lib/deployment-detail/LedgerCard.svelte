<script lang="ts">
	/**
	 * Orders & fills of one bot in one card. The switch keeps each list's own
	 * cursor paging; each list has distinct loading, empty, and error states
	 * with a retry of the page that failed.
	 */
	import Segmented from '$lib/Segmented.svelte';
	import type { DeploymentFill, DeploymentOrder } from '$lib/deployments';
	import { formatUtcTimestamp } from '$lib/time';
	import type { LedgerPager } from './ledger-pager.svelte';

	let {
		productId,
		orders,
		fills,
		view = $bindable()
	}: {
		/** The bot's product, shown for rows that do not name their own. */
		productId: string;
		orders: LedgerPager<DeploymentOrder>;
		fills: LedgerPager<DeploymentFill>;
		/** Which list is shown; owned by the page so it survives a reload. */
		view: 'orders' | 'fills';
	} = $props();
</script>

<section class="card ledger" aria-labelledby="ledger-title">
	<div class="card-head">
		<h2 id="ledger-title">Orders &amp; fills</h2>
		<Segmented
			label="Show orders or fills"
			options={[
				{ id: 'orders', label: 'Orders' },
				{ id: 'fills', label: 'Fills' }
			]}
			value={view}
			onchange={(next) => (view = next)}
			testId="ledger-switch"
		/>
	</div>
	{#if view === 'orders'}
		{#if orders.error}
			<p class="pad problem" role="status" data-testid="orders-error">
				Order history could not be loaded ({orders.error})
				<button
					type="button"
					class="btn"
					onclick={() => void orders.load(orders.cursors[orders.pageIndex], orders.pageIndex)}
				>
					Retry
				</button>
			</p>
		{:else if orders.loading && orders.page.rows.length === 0}
			<p class="pad quiet" data-testid="orders-loading">Loading orders…</p>
		{:else if orders.page.rows.length === 0}
			<p class="pad quiet" data-testid="orders-empty">No orders recorded for this deployment.</p>
		{:else}
			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<div class="table-scroll" tabindex="0" role="region" aria-label="Order history">
				<table>
					<caption class="sr-only">Order history</caption>
					<thead>
						<tr>
							<th scope="col">Time (UTC)</th>
							<th scope="col">Product</th>
							<th scope="col">Side</th>
							<th scope="col">Type</th>
							<th scope="col" class="num">Size</th>
							<th scope="col" class="num">Price</th>
							<th scope="col">Status</th>
						</tr>
					</thead>
					<tbody>
						{#each orders.page.rows as order (order.id)}
							<tr>
								<td class="mono muted">{formatUtcTimestamp(order.created_at).slice(5, 16)}</td>
								<td>{order.product_id || productId}</td>
								<td>{order.side}</td>
								<td class="muted">{order.kind}</td>
								<td class="num">{order.quantity}</td>
								<td class="num">{order.price ?? '—'}</td>
								<td class="muted"
									>{order.status}{order.reject_reason ? ` · ${order.reject_reason}` : ''}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<div class="pager" data-testid="orders-pager">
				<span>
					Showing {orders.page.rows.length} order{orders.page.rows.length === 1 ? '' : 's'}
					{orders.page.nextCursor !== null ? ' · more available' : ''}
				</span>
				<button
					type="button"
					disabled={orders.loading || orders.pageIndex === 0}
					onclick={() =>
						void orders.load(orders.cursors[orders.pageIndex - 1], orders.pageIndex - 1)}
					>Previous</button
				>
				<button
					type="button"
					disabled={orders.loading || orders.page.nextCursor === null}
					onclick={() =>
						void orders.load(orders.page.nextCursor ?? undefined, orders.pageIndex + 1)}
				>
					Next
				</button>
			</div>
		{/if}
	{:else if fills.error}
		<p class="pad problem" role="status" data-testid="fills-error">
			Fill history could not be loaded ({fills.error})
			<button
				type="button"
				class="btn"
				onclick={() => void fills.load(fills.cursors[fills.pageIndex], fills.pageIndex)}
			>
				Retry
			</button>
		</p>
	{:else if fills.loading && fills.page.rows.length === 0}
		<p class="pad quiet" data-testid="fills-loading">Loading fills…</p>
	{:else if fills.page.rows.length === 0}
		<p class="pad quiet" data-testid="fills-empty">No fills recorded for this deployment.</p>
	{:else}
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div class="table-scroll" tabindex="0" role="region" aria-label="Fill history">
			<table>
				<caption class="sr-only">Fill history</caption>
				<thead>
					<tr>
						<th scope="col">Time (UTC)</th>
						<th scope="col">Product</th>
						<th scope="col" class="num">Size</th>
						<th scope="col" class="num">Price</th>
						<th scope="col" class="num">Fee</th>
					</tr>
				</thead>
				<tbody>
					{#each fills.page.rows as fill (fill.id)}
						<tr>
							<td class="mono muted">{formatUtcTimestamp(fill.filled_at).slice(5, 16)}</td>
							<td>{fill.product_id || productId}</td>
							<td class="num">{fill.quantity}</td>
							<td class="num">{fill.price}</td>
							<td class="num">{fill.fee}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<div class="pager" data-testid="fills-pager">
			<span>
				Showing {fills.page.rows.length} fill{fills.page.rows.length === 1 ? '' : 's'}
				{fills.page.nextCursor !== null ? ' · more available' : ''}
			</span>
			<button
				type="button"
				disabled={fills.loading || fills.pageIndex === 0}
				onclick={() => void fills.load(fills.cursors[fills.pageIndex - 1], fills.pageIndex - 1)}
				>Previous</button
			>
			<button
				type="button"
				disabled={fills.loading || fills.page.nextCursor === null}
				onclick={() => void fills.load(fills.page.nextCursor ?? undefined, fills.pageIndex + 1)}
			>
				Next
			</button>
		</div>
	{/if}
</section>

<style>
	.card {
		margin-bottom: 16px;
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head h2 {
		margin-right: auto;
	}
	.pad {
		margin: 0;
		padding: 14px 16px;
	}
	.quiet {
		color: var(--muted);
	}
	.muted {
		color: var(--muted);
	}
	.problem {
		color: var(--neg);
	}
	.pager {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 16px;
		border-top: 1px solid var(--line);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.pager button {
		padding: 5px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		color: var(--text);
		cursor: pointer;
	}
	.pager button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.table-scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
	}
	th,
	td {
		padding: 9px 16px;
		text-align: left;
		font-size: var(--fs-sm);
	}
	th.num,
	td.num {
		text-align: right;
	}
	td {
		border-top: 1px solid var(--line);
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
