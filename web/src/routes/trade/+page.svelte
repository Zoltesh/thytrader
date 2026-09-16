<script lang="ts">
	import { onMount } from 'svelte';
	import { listDeployments, placeDiscretionaryOrder, type Deployment } from '$lib/deployments';
	import { EXECUTION_TIMEFRAMES, type ExecutionTimeframe } from '$lib/strategies';

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
	let submitting = $state(false);
	let error = $state<string | null>(null);
	let result = $state<Deployment | null>(null);
	let books = $state<Deployment[]>([]);
	let hydrated = $state(false);

	async function refreshBooks(): Promise<void> {
		try {
			const deployments = await listDeployments();
			books = deployments.filter((item) => item.kind === 'discretionary');
		} catch {
			books = [];
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
				paper_starting_cash: mode === 'paper' ? paperCash : undefined
			});
			await refreshBooks();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'The order could not be placed.';
		} finally {
			submitting = false;
		}
	}

	onMount(() => {
		hydrated = true;
		void refreshBooks();
	});
</script>

<svelte:head>
	<title>Trade · ThyTrader</title>
</svelte:head>

<main>
	<section class="hero">
		<div>
			<p class="eyebrow">On-demand</p>
			<h1>Place a long or short with SL/TP</h1>
			<p class="lede">
				Human origin over the same intent → risk → broker path as
				<code>thytrader-runtime place-order --confirm</code>. Live still requires Coinbase
				credentials. Live shorts need available base; they never borrow. Timeouts are
				reconciled, never retried.
			</p>
		</div>
	</section>

	<form
		class="ticket"
		data-testid="discretionary-ticket"
		data-hydrated={hydrated ? 'true' : 'false'}
	>
		<label>
			Product
			<input bind:value={productId} required pattern={'[A-Z0-9]{2,20}-USD'} />
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
		{/if}
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
</main>

<style>
	.hero {
		margin-bottom: 1.5rem;
	}
	.eyebrow {
		font-size: 0.8125rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: #a0aec0;
		margin: 0 0 0.25rem 0;
	}
	h1 {
		font-size: 2rem;
		margin: 0 0 0.5rem 0;
		color: #f7fafc;
	}
	.lede {
		color: #a0aec0;
		margin: 0;
		max-width: 42rem;
	}
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
		color: #a0aec0;
		font-size: 0.75rem;
		text-transform: uppercase;
	}
	input,
	select,
	button {
		font: inherit;
		text-transform: none;
	}
	input,
	select {
		background: #11181c;
		color: #e9edf1;
		border: 1px solid #2a3438;
		border-radius: 0.375rem;
		padding: 0.5rem 0.65rem;
	}
	button {
		align-self: end;
		background: #2b6cb0;
		color: #fff;
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
		border: 1px solid #c53030;
		border-radius: 0.5rem;
		background: #2d1b1b;
		color: #feb2b2;
	}
	.panel {
		margin-top: 1.5rem;
		padding: 1rem;
		border: 1px solid #20282a;
		border-radius: 0.5rem;
	}
	.label {
		margin: 0 0 0.5rem 0;
		color: #a0aec0;
		font-size: 0.75rem;
		text-transform: uppercase;
	}
	.empty,
	.panel p,
	.panel li {
		color: #e9edf1;
	}
</style>
