<script lang="ts">
	/**
	 * Home futures card (ADR 0127): the read-only Coinbase CFM futures account from
	 * `GET /api/v1/operator/futures-account`. Buying power, margin ratio, liquidation
	 * buffer and funding PnL in USD, plus the fact that buying power is shared with the
	 * USDC spot balance. The account section is hidden when futures were never observed
	 * (demo, no database). Below it, the paper futures books from
	 * `GET /api/v1/operator/futures-books` (ADR 0129); the card shows whenever either
	 * source has something to say. ThyTrader places no futures orders; nothing here acts.
	 */
	import { onMount } from 'svelte';
	import { futuresBooksSection } from '$lib/futures-book';
	import { fetchFuturesBooks } from '$lib/futures-book-api';
	import FuturesBooksSection from './FuturesBooksSection.svelte';
	import { fetchFuturesAccount, futuresCardView } from './futures';
	import { Resource } from './resource.svelte';

	const account = new Resource(fetchFuturesAccount);
	const books = new Resource(fetchFuturesBooks);
	const view = $derived(account.state.data === null ? null : futuresCardView(account.state.data));
	const booksView = $derived(
		books.state.data === null ? null : futuresBooksSection(books.state.data)
	);
	const accountShown = $derived(view !== null && view.kind === 'account');

	onMount(() => {
		void account.reload();
		void books.reload();
	});

	const ENABLEMENT_LABEL = {
		enabled: 'Enabled',
		not_enabled: 'Not enabled',
		unknown: 'Unknown'
	} as const;
</script>

{#if accountShown || booksView !== null}
	<section
		class="card futures"
		aria-labelledby={accountShown ? 'futures-title' : 'futures-books-title'}
		data-testid="futures-card"
	>
		{#if view !== null && view.kind === 'account'}
			<div class="head">
				<h2 id="futures-title">Futures (read-only)</h2>
				<span class="chip enablement-{view.enablement}" data-testid="futures-enablement">
					{ENABLEMENT_LABEL[view.enablement]}
				</span>
				{#if view.stale}<span class="chip warn">Stale snapshot</span>{/if}
			</div>
			{#if view.enablement === 'unknown'}
				<p class="warn" role="status" data-testid="futures-unknown">
					The futures balance could not be read, so values are unknown, not zero.
				</p>
			{:else if view.enablement === 'not_enabled'}
				<p class="muted" data-testid="futures-not-enabled">
					Coinbase reports no futures access for this account.
				</p>
			{/if}
			{#if view.enablement === 'enabled'}
				<dl>
					{#each view.facts as fact (fact.label)}
						<div>
							<dt>{fact.label}</dt>
							<dd>{fact.value}</dd>
							{#if fact.hint}<dd class="hint">{fact.hint}</dd>{/if}
						</div>
					{/each}
				</dl>
			{/if}
			<p class="note" data-testid="futures-shared-note">{view.sharedNote}</p>
			<p class="positions" data-testid="futures-positions">{view.positions}</p>
			{#if view.failures.length > 0}
				<p class="warn">Failed reads: {view.failures.join(', ')}</p>
			{/if}
			{#if account.state.status === 'error'}
				<p class="warn" role="status">Couldn't refresh: {account.state.error}</p>
			{/if}
		{/if}
		{#if booksView !== null}
			<FuturesBooksSection section={booksView} />
		{/if}
	</section>
{/if}

<style>
	.futures {
		display: grid;
		gap: 10px;
		padding: 12px 16px;
	}
	.head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
	}
	h2 {
		font-size: var(--fs-base);
	}
	dl {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
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
	.note {
		margin: 0;
		color: var(--text);
		font-size: var(--fs-sm);
	}
	.positions,
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
</style>
