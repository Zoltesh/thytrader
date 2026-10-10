<script lang="ts">
	/**
	 * Home "Paper futures books" (ADR 0129) from the operator `futures-books` report:
	 * each book that is not stopped with its side and contracts, equity and liquidation
	 * buffer (USD), a warning chip when entries are denied or evidence is unknown, and a
	 * link to the bot. The envelope line shows the paper cash committed against the
	 * policy's paper futures capital. USD here is never added to USDC.
	 */
	import { resolve } from '$app/paths';
	import type { FuturesBooksSection } from '$lib/futures-book';

	let { section }: { section: FuturesBooksSection } = $props();
</script>

<div class="books" data-testid="futures-books">
	<h3 id="futures-books-title">Paper futures books (USD)</h3>
	<p class="envelope" data-testid="futures-books-envelope">
		{section.committed} committed of {section.capital} paper futures capital
	</p>
	<ul role="list">
		{#each section.rows as row (row.id)}
			<li data-testid="futures-book-row">
				<a href={resolve(`/deployments/${encodeURIComponent(row.id)}`)}>
					<span class="name">{row.name}</span>
					<span class="faint">{row.product} · {row.status}</span>
				</a>
				<span>{row.position}</span>
				<span class="mono">Equity {row.equity}</span>
				<span class="mono">Buffer {row.buffer}</span>
				{#if row.warning}<span class="chip warn" data-testid="futures-book-warning"
						>{row.warning}</span
					>{/if}
			</li>
		{/each}
	</ul>
	<p class="note">{section.collateralNote}</p>
</div>

<style>
	.books {
		display: grid;
		gap: 8px;
	}
	h3 {
		margin: 0;
		font-size: var(--fs-sm);
		font-weight: 600;
	}
	.envelope,
	.note {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	ul {
		display: grid;
		gap: 6px;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 14px;
		padding: 6px 0;
		border-top: 1px solid var(--line);
		font-size: var(--fs-sm);
	}
	a {
		display: grid;
		margin-right: auto;
		color: var(--text);
		text-decoration: none;
	}
	a:hover .name {
		text-decoration: underline;
	}
	.faint {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.mono {
		font-family: var(--font-mono);
	}
	.chip.warn {
		border-color: var(--warn-line);
		color: var(--warn);
	}
</style>
