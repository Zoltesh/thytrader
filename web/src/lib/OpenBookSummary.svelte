<script lang="ts">
	/**
	 * One open book in two compact lines (ADR 0098): a state chip, the gross
	 * unrealized PnL at the last evaluated bar, and time since entry; then the
	 * entry, stop, and target. Used on Portfolio sleeve rows. The chip's word
	 * names the state, so color is never the only signal.
	 */
	import { groupIntegerDigits } from '$lib/money';
	import {
		bookSizeText,
		bookStateChip,
		bookStateTitle,
		heldText,
		markTitle,
		unrealizedText,
		type OpenBook
	} from '$lib/open-books';

	let {
		book,
		quote,
		now,
		showProduct = false
	}: {
		book: OpenBook;
		/** Quote currency for the unrealized PnL. */
		quote: string;
		/** Clock for the held time (ms since epoch). */
		now: number;
		/** Name the product (multi-book sleeves). */
		showProduct?: boolean;
	} = $props();

	const chip = $derived(bookStateChip(book.position_state));
	const pnl = $derived(unrealizedText(book, quote));
</script>

<div class="book" data-testid="open-book">
	<div class="line">
		<span class="state {chip.tone}" title={bookStateTitle(book)} data-testid="open-book-state"
			>{chip.text}</span
		>
		{#if pnl !== null}
			<span class="pnl {pnl.tone}" title={markTitle(book)} data-testid="open-book-pnl"
				>{pnl.text}</span
			>
		{:else}
			<span class="faint" title={markTitle(book)}>uPnL —</span>
		{/if}
		<span
			class="faint"
			title="Held since the {book.entered_bar.slice(0, 16)} UTC entry bar"
			data-testid="open-book-held">· {heldText(book.entered_bar, now)}</span
		>
	</div>
	<div class="levels" data-testid="open-book-levels">
		<span class="size"
			>{#if showProduct}{book.product_id}
			{/if}{bookSizeText(book)}</span
		>
		<span title="Entry price"><span class="k">E</span>{groupIntegerDigits(book.entry_price)}</span>
		<span title="Stop"><span class="k">SL</span>{groupIntegerDigits(book.stop_price)}</span>
		<span title="Take-profit"
			><span class="k">TP</span>{book.target_price === null
				? 'none'
				: groupIntegerDigits(book.target_price)}</span
		>
	</div>
</div>

<style>
	.book {
		display: grid;
		gap: 3px;
		min-width: 190px;
		margin-top: 4px;
		font-size: var(--fs-sm);
	}
	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
	}
	.state {
		display: inline-flex;
		align-items: center;
		height: 18px;
		padding: 0 6px;
		border-radius: var(--radius-pill);
		background: var(--surface-2);
		color: var(--muted);
		font-size: var(--fs-xs);
		font-weight: 600;
	}
	.state.ok {
		background: var(--accent-soft);
		color: var(--accent);
	}
	.state.warn {
		background: var(--warn-soft);
		color: var(--warn);
	}
	.state.bad {
		background: var(--danger-soft);
		color: var(--neg);
	}
	.muted {
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
	}
	.pnl {
		font-variant-numeric: tabular-nums;
		font-weight: 600;
	}
	.pnl.pos {
		color: var(--pos);
	}
	.pnl.neg {
		color: var(--neg);
	}
	.pnl.muted {
		color: var(--muted);
	}
	.levels {
		display: flex;
		flex-wrap: wrap;
		column-gap: 10px;
		color: var(--muted);
		font-size: var(--fs-xs);
		font-variant-numeric: tabular-nums;
	}
	.levels > span {
		white-space: nowrap;
	}
	.size {
		color: var(--text);
	}
	.k {
		margin-right: 4px;
		color: var(--faint);
		font-size: 10px;
		font-weight: 600;
		letter-spacing: 0.04em;
	}
</style>
