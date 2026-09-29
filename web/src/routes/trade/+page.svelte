<script lang="ts">
	import PageHead from '$lib/PageHead.svelte';
	import { onMount } from 'svelte';
	import TradeReasonReview from '$lib/TradeReasonReview.svelte';
	import { listDeployments, placeDiscretionaryOrder, type Deployment } from '$lib/deployments';
	import { fetchTradeReasons, type TradeReasonRecord } from '$lib/memory';
	import { EXECUTION_TIMEFRAMES, type ExecutionTimeframe } from '$lib/strategies';
	import {
		PAPER_DEFAULT_MAKER_FEE_RATE,
		PAPER_DEFAULT_TAKER_FEE_RATE,
		PAPER_FEE_ENGINE_NOTE
	} from '$lib/fees';

	let productId = $state('BTC-USD');
	let mode = $state<'paper' | 'live'>('paper');
	let side = $state<'long' | 'short'>('long');
	let timeframe = $state<ExecutionTimeframe>('5m');
	let entryKind = $state<'post_only_limit' | 'marketable'>('post_only_limit');
	let limitPrice = $state('');
	let quantity = $state('');
	let quoteNotional = $state('');
	let stopPrice = $state('');
	let takeProfitPrice = $state('');
	let paperCash = $state('10000');
	let paperMakerFee = $state(PAPER_DEFAULT_MAKER_FEE_RATE);
	let paperTakerFee = $state(PAPER_DEFAULT_TAKER_FEE_RATE);
	let note = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);
	let result = $state<Deployment | null>(null);
	let books = $state<Deployment[]>([]);
	let tradeReasons = $state<TradeReasonRecord[]>([]);
	let hydrated = $state(false);

	async function refreshBooks(): Promise<void> {
		try {
			const deployments = await listDeployments();
			books = deployments.filter((item) => item.kind === 'discretionary');
		} catch {
			books = [];
		}
	}

	async function refreshReasons(deploymentId?: string): Promise<void> {
		try {
			tradeReasons = await fetchTradeReasons(
				deploymentId === undefined ? undefined : { deploymentId }
			);
		} catch {
			tradeReasons = [];
		}
	}

	async function submitOrder(): Promise<void> {
		submitting = true;
		error = null;
		result = null;
		try {
			if ((quantity === '') === (quoteNotional === '')) {
				throw new Error('Provide exactly one of quantity or quote notional.');
			}
			if (entryKind === 'post_only_limit' && limitPrice === '') {
				throw new Error('Post-only entries require a limit price.');
			}
			if (mode === 'live') {
				const confirmed = window.confirm(
					`PLACE A LIVE ORDER on Coinbase?\n\nThis submits a REAL ${side} spot order on ${productId} using your Coinbase API keys.\n\nYou are solely responsible for all trades and market risk.`
				);
				if (!confirmed) return;
			}
			const trimmedNote = note.trim();
			result = await placeDiscretionaryOrder({
				mode,
				product_id: productId,
				side,
				stop_price: stopPrice,
				take_profit_price: takeProfitPrice,
				idempotency_key: crypto.randomUUID(),
				origin: 'human',
				entry_kind: entryKind,
				timeframe,
				quantity: quantity === '' ? undefined : quantity,
				quote_notional: quoteNotional === '' ? undefined : quoteNotional,
				limit_price: limitPrice === '' ? undefined : limitPrice,
				paper_starting_cash: mode === 'paper' ? paperCash : undefined,
				maker_fee_rate: mode === 'paper' ? paperMakerFee : undefined,
				taker_fee_rate: mode === 'paper' ? paperTakerFee : undefined,
				note: trimmedNote === '' ? undefined : trimmedNote,
				// Reached only after the PLACE A LIVE ORDER confirmation above was accepted.
				i_understand_live: mode === 'live' ? true : undefined
			});
			await refreshBooks();
			await refreshReasons(result.id);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'The order could not be placed.';
		} finally {
			submitting = false;
		}
	}

	onMount(() => {
		hydrated = true;
		void refreshBooks();
		void refreshReasons();
	});
</script>

<svelte:head>
	<title>Trade · ThyTrader</title>
</svelte:head>

<main>
	<PageHead eyebrow="On-demand" title="Place a long or short with SL/TP">
		{#snippet intro()}
			<p class="lede">
				Human origin over the same intent → risk → broker path as
				<code>thytrader-runtime place-order --confirm</code>. Optional note is frozen onto the
				why-trade record. Live still requires Coinbase credentials. Live shorts need available base;
				they never borrow. Timeouts are reconciled, never retried.
			</p>
		{/snippet}
	</PageHead>

	<form
		class="ticket"
		data-testid="discretionary-ticket"
		data-hydrated={hydrated ? 'true' : 'false'}
	>
		<label>
			Product
			<input bind:value={productId} required pattern={'[A-Z0-9]{2,20}-(?:USD|USDC)'} />
		</label>
		<label>
			Mode
			<select bind:value={mode}>
				<option value="paper">Paper</option>
				<option value="live">Live</option>
			</select>
		</label>
		<label>
			Side
			<select bind:value={side} data-testid="discretionary-side">
				<option value="long">Long</option>
				<option value="short">Short</option>
			</select>
		</label>
		<label>
			Clock
			<select bind:value={timeframe}>
				{#each EXECUTION_TIMEFRAMES as clock (clock)}
					<option value={clock}>{clock}</option>
				{/each}
			</select>
		</label>
		<label>
			Entry
			<select bind:value={entryKind}>
				<option value="post_only_limit">Post-only limit</option>
				<option value="marketable">Marketable</option>
			</select>
		</label>
		<label>
			Limit price
			<input bind:value={limitPrice} inputmode="decimal" placeholder="Required for maker" />
		</label>
		<label>
			Quantity
			<input bind:value={quantity} inputmode="decimal" />
		</label>
		<label>
			Quote notional
			<input bind:value={quoteNotional} inputmode="decimal" />
		</label>
		<label>
			Stop loss
			<input bind:value={stopPrice} required inputmode="decimal" />
		</label>
		<label>
			Take profit
			<input bind:value={takeProfitPrice} required inputmode="decimal" />
		</label>
		{#if mode === 'paper'}
			<label>
				Paper cash
				<input bind:value={paperCash} required inputmode="decimal" />
			</label>
			<label>
				Maker fee rate
				<input bind:value={paperMakerFee} required inputmode="decimal" />
			</label>
			<label>
				Taker fee rate
				<input bind:value={paperTakerFee} required inputmode="decimal" />
			</label>
			<p class="lede">{PAPER_FEE_ENGINE_NOTE}</p>
		{/if}
		<label class="note">
			Why note
			<textarea
				bind:value={note}
				data-testid="discretionary-note"
				maxlength="4000"
				placeholder="Optional. Frozen onto the why-trade record."></textarea>
		</label>
		<button type="button" disabled={submitting} onclick={() => void submitOrder()}>
			{submitting ? 'Submitting…' : side === 'short' ? 'Place short' : 'Place long'}
		</button>
	</form>

	{#if error}
		<div class="error-banner" role="alert">
			<strong>Order not accepted</strong>
			<p>{error}</p>
		</div>
	{/if}

	{#if result}
		<section class="panel" data-testid="discretionary-result">
			<p class="label">Last snapshot</p>
			<p>{result.id}</p>
			<p>{result.mode} · {result.status} · {result.phase}</p>
			<p>{result.orders.length} orders · {result.fills.length} fills</p>
		</section>
	{/if}

	<section class="panel">
		<p class="label">Discretionary books</p>
		{#if books.length === 0}
			<p class="empty">No discretionary books yet.</p>
		{:else}
			<ul>
				{#each books as book (book.id)}
					<li>{book.product_id} · {book.mode} · {book.status} · {book.phase}</li>
				{/each}
			</ul>
		{/if}
	</section>

	<TradeReasonReview
		records={tradeReasons}
		emptyMessage="Why-trade records for this ticket appear after an intent is persisted."
	/>
</main>

<style>
	.ticket {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
		gap: 1rem;
		margin-top: 1.5rem;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		color: var(--muted);
		font-size: 0.75rem;
		text-transform: uppercase;
	}
	input,
	select,
	textarea,
	button {
		font: inherit;
		text-transform: none;
	}
	input,
	select,
	textarea {
		background: var(--surface);
		color: var(--text);
		border: 1px solid var(--line-2);
		border-radius: 0.375rem;
		padding: 0.5rem 0.65rem;
	}
	.note {
		grid-column: 1 / -1;
	}
	textarea {
		min-height: 4.5rem;
		resize: vertical;
	}
	button {
		align-self: end;
		background: var(--accent);
		color: var(--accent-ink);
		border: none;
		padding: 0.65rem 1rem;
		border-radius: 0.375rem;
		font-weight: 600;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.error-banner {
		margin-top: 1.5rem;
		padding: 1rem;
		border: 1px solid var(--neg);
		border-radius: 0.5rem;
		background: var(--danger-soft);
		color: var(--neg);
	}
	.panel {
		margin-top: 1.5rem;
		padding: 1rem;
		border: 1px solid var(--line);
		border-radius: 0.5rem;
	}
	.label {
		margin: 0 0 0.5rem 0;
		color: var(--muted);
		font-size: 0.75rem;
		text-transform: uppercase;
	}
	.empty,
	.panel p,
	.panel li {
		color: var(--text);
	}
</style>
