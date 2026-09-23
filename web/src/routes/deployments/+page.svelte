<script lang="ts">
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import { listDeploymentsPage, type Deployment } from '$lib/deployments';
	import { lifecycleContractNote, lifecycleControlsAvailable } from '$lib/lifecycle-contract';

	const PAGE_SIZE = 50;

	let pageRows = $state<Deployment[]>([]);
	let hasMore = $state(false);
	let offset = $state(0);
	/** Distinguishes a truly empty inventory from an exhausted trailing page. */
	let everLoaded = $state(false);
	let listLoading = $state(true);
	let listError = $state<string | null>(null);

	// Grouping order: running, paused, needs attention, stopped.
	const running = $derived(pageRows.filter((deployment) => deployment.status === 'running'));
	const paused = $derived(pageRows.filter((deployment) => deployment.status === 'paused'));
	const attention = $derived(
		pageRows.filter(
			(deployment) =>
				deployment.status !== 'running' &&
				deployment.status !== 'paused' &&
				deployment.status !== 'stopped'
		)
	);
	const stoppedItems = $derived(pageRows.filter((deployment) => deployment.status === 'stopped'));

	/** Whether the current page is full: a Next control would have rows to show. */
	const canGoNext = $derived(hasMore && pageRows.length > 0);

	async function loadPage(targetOffset: number): Promise<void> {
		listLoading = true;
		listError = null;
		try {
			const result = await listDeploymentsPage(PAGE_SIZE, targetOffset);
			// Fail closed on a page that claims more while being empty.
			if (result.deployments.length === 0 && result.hasMore) {
				throw new Error('Deployment inventory returned an empty page while claiming more rows.');
			}
			pageRows = result.deployments;
			hasMore = result.hasMore;
			offset = targetOffset;
			if (result.deployments.length > 0) everLoaded = true;
		} catch (caught) {
			listError = caught instanceof Error ? caught.message : 'Could not load deployments.';
		} finally {
			listLoading = false;
		}
	}

	function nextPage(): void {
		if (!canGoNext || listLoading) return;
		void loadPage(offset + PAGE_SIZE);
	}

	function previousPage(): void {
		if (offset === 0 || listLoading) return;
		void loadPage(Math.max(0, offset - PAGE_SIZE));
	}

	onMount(() => {
		void loadPage(0);
	});
</script>

<svelte:head><title>Deployments · ThyTrader</title></svelte:head>

<main>
	<section class="page-head">
		<div>
			<p class="eyebrow">Runtime status</p>
			<h1>Deployments</h1>
			<p class="lede">
				Everything running on this workstation. Open a deployment for exact-version evidence,
				positions, orders, and fills. Start new deployments from
				<a href={resolve('/deploy')}>Deploy</a>.
			</p>
		</div>
	</section>

	{#if listLoading}
		<section class="loading-card" aria-label="Loading deployments">
			<div class="skeleton wide"></div>
			<div class="skeleton"></div>
		</section>
	{:else if listError}
		<div class="error-banner" role="alert">
			<div>
				<strong>Couldn't load deployments</strong>
				<p>{listError}</p>
			</div>
			<button type="button" onclick={() => void loadPage(offset)}>Try again</button>
		</div>
	{:else if pageRows.length === 0 && !everLoaded}
		<section class="empty-state">
			<h2>No deployments yet</h2>
			<p>
				Deploy a published strategy to run it automatically, or place a one-off order on Trade.
				Deployments started here or by an agent appear here.
			</p>
			<a class="link-button" href={resolve('/deploy')}>Open Deploy</a>
		</section>
	{:else if pageRows.length === 0}
		<section class="empty-state" data-testid="trailing-empty-page">
			<h2>No deployments on this page</h2>
			<p>
				Rows past here were removed from the inventory while you were paging. Go back a page or
				return to the first page.
			</p>
			<button type="button" class="link-button" onclick={() => void loadPage(0)}>
				Back to first page
			</button>
		</section>
	{:else}
		{#if running.length > 0}
			<h2 class="group-heading">Running</h2>
			<ul class="stack" role="list">
				{#each running as deployment (deployment.id)}
					{@render card(deployment)}
				{/each}
			</ul>
		{/if}
		{#if paused.length > 0}
			<h2 class="group-heading">Paused</h2>
			<ul class="stack" role="list">
				{#each paused as deployment (deployment.id)}
					{@render card(deployment)}
				{/each}
			</ul>
		{/if}
		{#if attention.length > 0}
			<h2 class="group-heading">Needs attention</h2>
			<ul class="stack" role="list">
				{#each attention as deployment (deployment.id)}
					{@render card(deployment)}
				{/each}
			</ul>
		{/if}
		{#if stoppedItems.length > 0}
			<h2 class="group-heading">Stopped</h2>
			<ul class="stack" role="list">
				{#each stoppedItems as deployment (deployment.id)}
					{@render card(deployment)}
				{/each}
			</ul>
		{/if}
		<nav class="inventory-pager" aria-label="Deployment inventory pagination">
			<span
				>Showing {pageRows.length} deployment{pageRows.length === 1 ? '' : 's'} from {offset +
					1}</span
			>
			<button
				type="button"
				onclick={previousPage}
				disabled={listLoading || offset === 0}
				aria-label="Previous deployment page">Previous</button
			>
			<button
				type="button"
				onclick={nextPage}
				disabled={listLoading || !canGoNext}
				aria-label="Next deployment page">Next</button
			>
		</nav>
	{/if}
</main>

{#snippet card(deployment: Deployment)}
	<li class="deploy-card">
		<header class="card-head">
			<div class="title">
				<span class="mode mode-{deployment.mode}">{deployment.mode}</span>
				<h2>
					<a href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
						>{deployment.product_id}</a
					>
				</h2>
				<span class="meta">{deployment.timeframe ?? '—'} · {deployment.status}</span>
			</div>
		</header>
		<div class="facts">
			<div><span>Lifecycle</span><strong>{deployment.lifecycle_command}</strong></div>
			<div><span>Cash</span><strong>{deployment.cash}</strong></div>
			{#if deployment.last_signal}
				<div><span>Last signal</span><strong>{deployment.last_signal}</strong></div>
			{/if}
			{#if deployment.last_evaluated_bar}
				<div><span>Last bar</span><strong>{deployment.last_evaluated_bar}</strong></div>
			{/if}
		</div>
		{#if deployment.mismatch_detail}
			<p class="problem" role="alert">{deployment.mismatch_detail}</p>
		{/if}
		{#if deployment.daily_loss_latched || deployment.drawdown_latched}
			<p class="problem" role="status">
				{[
					deployment.daily_loss_latched ? 'daily loss breaker latched' : null,
					deployment.drawdown_latched ? 'drawdown breaker latched' : null
				]
					.filter(Boolean)
					.join(' · ')}
			</p>
		{/if}
		{#if !lifecycleControlsAvailable(deployment)}
			<p class="contract-note">{lifecycleContractNote(deployment)}</p>
		{/if}
		<p class="card-actions">
			<a
				class="bar-button"
				href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
				aria-label="Open {deployment.product_id} deployment detail">Detail →</a
			>
		</p>
	</li>
{/snippet}

<style>
	.page-head {
		display: flex;
		justify-content: space-between;
		align-items: end;
		gap: 18px;
		margin-bottom: 28px;
	}
	.group-heading {
		margin: 26px 0 12px;
		font-size: 13px;
		color: #778386;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	.stack {
		display: grid;
		gap: 12px;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.deploy-card {
		border: 1px solid #232b2d;
		background: linear-gradient(145deg, rgba(20, 26, 28, 0.95), rgba(12, 16, 18, 0.95));
		border-radius: 13px;
		padding: 18px 20px;
		display: grid;
		gap: 12px;
	}
	.card-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 12px;
	}
	.title {
		display: flex;
		align-items: baseline;
		gap: 10px;
		flex-wrap: wrap;
	}
	.title h2 {
		margin: 0;
		font-size: 18px;
	}
	.title h2 a {
		color: #dce4e5;
		text-decoration: none;
	}
	.title h2 a:hover {
		color: #5ce1b5;
	}
	.meta {
		color: #778386;
		font-size: 12px;
	}
	.mode {
		font:
			600 10px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		border-radius: 6px;
		padding: 3px 7px;
	}
	.mode-paper {
		color: #9fd9ff;
		border: 1px solid #2c4a5c;
		background: #10222c;
	}
	.mode-live {
		color: #ffb3b3;
		border: 1px solid #733d3d;
		background: #2c1212;
	}
	.facts {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 26px;
	}
	.facts span {
		display: block;
		color: #7d8a8d;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	.facts strong {
		font:
			500 13px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		color: #dce4e5;
	}
	.problem {
		margin: 0;
		color: #f0a3a3;
		font-size: 13px;
	}
	.contract-note {
		margin: 0;
		color: #b39b72;
		font-size: 12px;
	}
	.card-actions {
		margin: 0;
	}
	.bar-button {
		display: inline-block;
		border: 1px solid #303a3c;
		background: #151b1d;
		color: #dce4e5;
		border-radius: 8px;
		padding: 7px 12px;
		font: inherit;
		font-size: 12px;
		text-decoration: none;
		cursor: pointer;
	}
	.bar-button:hover {
		border-color: #5ce1b5;
	}
	.inventory-pager {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 22px;
		color: #7d8a8d;
		font-size: 12px;
	}
	.inventory-pager button {
		color: #dce4e5;
		background: #151b1d;
		border: 1px solid #303a3c;
		border-radius: 7px;
		padding: 7px 11px;
		cursor: pointer;
		font-size: 12px;
	}
	.inventory-pager button:hover:not(:disabled) {
		border-color: #5ce1b5;
	}
	.inventory-pager button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.empty-state .link-button {
		border: none;
		background: none;
		font: inherit;
		cursor: pointer;
		padding: 0;
	}
</style>
