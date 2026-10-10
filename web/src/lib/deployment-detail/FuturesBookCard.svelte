<script lang="ts">
	/**
	 * Paper futures book of a futures bot (ADR 0129 §4) from
	 * `GET /api/v1/deployments/{id}/futures`: the bound contract, side and contracts,
	 * entry and mark, equity, cash, notional, leverage against the policy maximum,
	 * overnight margin, the liquidation buffer against the policy minimum, the
	 * liquidation price, funding, and why new entries are denied. Every amount is USD
	 * and unknown values read "Unknown", never zero. The detail page renders this card
	 * only for futures bots; it reads its own endpoint and re-reads after each revision.
	 */
	import { untrack } from 'svelte';
	import { PAPER_ONLY_NOTE, SHARED_COLLATERAL_NOTE, futuresBookView } from '$lib/futures-book';
	import { fetchDeploymentFutures } from '$lib/futures-book-api';
	import { Resource } from '$lib/home/resource.svelte';

	let { deploymentId, revision }: { deploymentId: string; revision: number } = $props();

	const book = new Resource(() => fetchDeploymentFutures(deploymentId));
	const view = $derived(book.state.data === null ? null : futuresBookView(book.state.data));

	$effect(() => {
		void deploymentId;
		void revision;
		// The reload reads the resource's own state; only the id and revision re-trigger it.
		untrack(() => void book.reload());
	});
</script>

<section class="card futures-book" aria-labelledby="futures-book-title" data-testid="futures-book">
	<div class="card-head">
		<h2 id="futures-book-title">Paper futures book (USD)</h2>
		<span class="chip paper">Paper only</span>
		{#if view?.latched}<span class="chip warn-chip">Daily-loss breaker latched</span>{/if}
	</div>
	<div class="body">
		<p class="note" data-testid="futures-paper-only">{PAPER_ONLY_NOTE}</p>
		{#if view === null}
			{#if book.state.status === 'error'}
				<div class="warn" role="status">
					The futures book could not be read: {book.state.error}
					<button type="button" class="btn" onclick={() => void book.reload()}>Retry</button>
				</div>
			{:else}
				<div class="skeleton" aria-label="Loading the futures book"></div>
			{/if}
		{:else}
			{#if view.entryBlocks}
				<p class="warn" role="status" data-testid="futures-entry-blocks">{view.entryBlocks}</p>
			{/if}
			{#if view.unknown}
				<p class="warn" data-testid="futures-unknown">{view.unknown}</p>
			{/if}
			{#each view.groups as group (group.id)}
				<h3>{group.title}</h3>
				<dl data-testid="futures-{group.id}">
					{#each group.facts as fact (fact.id)}
						<div data-testid="futures-fact-{fact.id}">
							<dt>{fact.label}</dt>
							<dd>{fact.value}</dd>
							{#if fact.hint}<dd class="hint">{fact.hint}</dd>{/if}
						</div>
					{/each}
				</dl>
			{/each}
			{#if view.fundingOverdue}<p class="warn">{view.fundingOverdue}</p>{/if}
			{#if view.fundingRows.length > 0}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<div class="table-scroll" tabindex="0" role="region" aria-label="Recent funding">
					<table data-testid="futures-funding-table">
						<caption class="sr-only">Recent funding hours, newest first (USD)</caption>
						<thead>
							<tr>
								<th scope="col">Hour</th>
								<th scope="col" class="num">Position</th>
								<th scope="col" class="num">Mark</th>
								<th scope="col" class="num">Rate</th>
								<th scope="col" class="num">Amount</th>
							</tr>
						</thead>
						<tbody>
							{#each view.fundingRows as row (row.key)}
								<tr>
									<td>{row.time}</td>
									<td class="num">{row.quantity}</td>
									<td class="num">{row.mark}</td>
									<td class="num">{row.rate}</td>
									<td class="num {row.tone}">{row.amount}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<p class="muted">No funding charged yet.</p>
			{/if}
			{#if book.state.status === 'error'}
				<p class="warn" role="status">Couldn't refresh: {book.state.error}</p>
			{/if}
		{/if}
		<p class="note" data-testid="futures-collateral-note">{SHARED_COLLATERAL_NOTE}</p>
	</div>
</section>

<style>
	.card {
		margin-bottom: 16px;
	}
	.card-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head h2 {
		margin-right: auto;
	}
	.warn-chip {
		border-color: var(--warn-line);
		color: var(--warn);
	}
	.body {
		display: grid;
		gap: 10px;
		padding: 12px 16px 16px;
	}
	h3 {
		margin: 6px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
		font-weight: 500;
		letter-spacing: 0.05em;
		text-transform: uppercase;
	}
	dl {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 10px 18px;
		margin: 0;
	}
	dt {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	dd {
		margin: 0;
		font-family: var(--font-mono);
	}
	dd.hint {
		color: var(--faint);
		font-family: inherit;
		font-size: var(--fs-sm);
	}
	.note,
	.muted {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.warn {
		margin: 0;
		color: var(--warn);
		font-size: var(--fs-sm);
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
		padding: 7px 10px;
		text-align: left;
		font-size: var(--fs-sm);
	}
	th.num,
	td.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	td {
		border-top: 1px solid var(--line);
	}
	td.pos {
		color: var(--pos);
	}
	td.neg {
		color: var(--neg);
	}
	td.muted {
		color: var(--muted);
	}
</style>
