<script lang="ts">
	/**
	 * Trade page outcome panels: the last placed order's deployment snapshot and
	 * the discretionary books inventory (or why it is incomplete).
	 */
	import type { Deployment } from '$lib/deployments';

	let {
		result,
		books,
		booksError
	}: {
		result: Deployment | null;
		books: Deployment[];
		booksError: string | null;
	} = $props();
</script>

{#if result}
	<section class="card panel" data-testid="discretionary-result">
		<p class="label">Last snapshot</p>
		<p>{result.id}</p>
		<p>{result.mode} · {result.status} · {result.phase}</p>
		<p>{result.orders.length} orders · {result.fills.length} fills</p>
	</section>
{/if}

<section class="card panel">
	<p class="label">Discretionary books</p>
	{#if booksError !== null}
		<p class="empty" data-testid="discretionary-books-incomplete">{booksError}</p>
	{:else if books.length === 0}
		<p class="empty">No discretionary books yet.</p>
	{:else}
		<ul>
			{#each books as book (book.id)}
				<li>{book.product_id} · {book.mode} · {book.status} · {book.phase}</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.panel {
		margin-top: 16px;
		padding: 14px 16px;
	}
	.label {
		margin: 0 0 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.panel p,
	.panel li {
		margin: 0;
		color: var(--text);
	}
	.panel ul {
		margin: 0;
		padding-left: 18px;
	}
	.empty {
		color: var(--muted);
	}
</style>
