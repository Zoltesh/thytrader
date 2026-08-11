<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { createDraft, publishDraft, submitBacktest, type StrategyDraft } from '$lib/strategies';

	let draft: StrategyDraft | null = $state(null);
	let publishedFingerprint = $state<string | null>(null);
	let datasetFingerprint = $state('');
	let evaluationStart = $state('');
	let evaluationEnd = $state('');
	let error = $state<string | null>(null);
	let loading = $state(true);
	let publishing = $state(false);
	let submitting = $state(false);

	async function startDraft(): Promise<void> {
		loading = true;
		error = null;
		try {
			draft = await createDraft();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not create a strategy draft.';
		} finally {
			loading = false;
		}
	}

	async function publish(): Promise<void> {
		if (!draft) return;
		publishing = true;
		error = null;
		try {
			const published = await publishDraft(draft);
			publishedFingerprint = published.strategy_fingerprint;
			draft = published.strategy;
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Strategy publication failed.';
		} finally {
			publishing = false;
		}
	}

	async function runBacktest(): Promise<void> {
		if (!publishedFingerprint) return;
		submitting = true;
		error = null;
		try {
			const result = await submitBacktest({
				strategy_fingerprint: publishedFingerprint,
				dataset_fingerprint: datasetFingerprint,
				evaluation_start: new Date(evaluationStart).toISOString(),
				evaluation_end: new Date(evaluationEnd).toISOString(),
				initial_quote_balance: '10000',
				maker_fee_rate: '0.001',
				taker_fee_rate: '0.002',
				fixed_slippage_bps: '10'
			});
			window.location.assign(
				resolve(`/backtests?result=${encodeURIComponent(result.result_fingerprint)}`)
			);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Backtest submission failed.';
		} finally {
			submitting = false;
		}
	}

	onMount(() => void startDraft());
</script>

<svelte:head><title>Strategies · ThyTrader</title></svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home"
			><span class="brand-mark">T</span><span>ThyTrader</span></a
		>
		<nav aria-label="Primary navigation">
			<a href={resolve('/')}>Portfolio</a><a class="active" href={resolve('/strategies')}
				>Strategies</a
			><a href={resolve('/backtests')}>Backtests</a>
		</nav>
		<div class="local-pill"><span></span> Research only</div>
	</header>
	<main>
		<section class="hero">
			<div>
				<p class="eyebrow">Conservative research</p>
				<h1>Strategy workspace</h1>
				<p class="lede">
					Draft, publish immutable evidence, then run a historical simulation. Nothing here deploys
					or trades.
				</p>
			</div>
		</section>
		{#if error}<div class="error-banner" role="alert">
				<div>
					<strong>Research operation unavailable</strong>
					<p>{error}</p>
				</div>
				<button type="button" onclick={startDraft}>Start a new draft</button>
			</div>{/if}
		{#if loading}<section class="loading-card" aria-label="Creating strategy draft">
				<div class="skeleton wide"></div>
			</section>{:else if draft}
			<section class="asset-panel strategy-form">
				<div class="panel-heading">
					<div>
						<h2>Reference EMA trend</h2>
						<p>BTC-USD · 1h · long-only · single position</p>
					</div>
					<span class="status">{draft.status}</span>
				</div>
				<div class="form-grid">
					<label
						>Strategy name<input
							bind:value={draft.name}
							disabled={draft.status !== 'draft'}
						/></label
					>
					<label
						>Risk per trade<input
							inputmode="decimal"
							bind:value={draft.sizing.risk_fraction}
							disabled={draft.status !== 'draft'}
						/></label
					>
					<label
						>Minimum USD notional<input
							inputmode="decimal"
							bind:value={draft.sizing.min_quote_notional}
							disabled={draft.status !== 'draft'}
						/></label
					>
					<label
						>Maximum USD notional<input
							inputmode="decimal"
							bind:value={draft.sizing.max_quote_notional}
							disabled={draft.status !== 'draft'}
						/></label
					>
				</div>
				<div class="action-row">
					<button
						class="refresh"
						type="button"
						onclick={publish}
						disabled={publishing || draft.status !== 'draft'}
						>{publishing ? 'Publishing…' : 'Validate & publish immutable version'}</button
					>{#if publishedFingerprint}<code>{publishedFingerprint}</code>{/if}
				</div>
			</section>
			{#if publishedFingerprint}<section class="asset-panel strategy-form">
					<div class="panel-heading">
						<div>
							<h2>Run historical backtest</h2>
							<p>Use a verified BTC-USD 1h dataset fingerprint and explicit evaluation window.</p>
						</div>
					</div>
					<div class="form-grid">
						<label
							>Dataset fingerprint<input
								bind:value={datasetFingerprint}
								placeholder="sha256:…"
							/></label
						><label
							>Evaluation start (UTC)<input
								type="datetime-local"
								bind:value={evaluationStart}
							/></label
						><label
							>Evaluation end (UTC)<input type="datetime-local" bind:value={evaluationEnd} /></label
						>
					</div>
					<div class="action-row">
						<button
							class="refresh"
							type="button"
							onclick={runBacktest}
							disabled={submitting || !datasetFingerprint || !evaluationStart || !evaluationEnd}
							>{submitting ? 'Running deterministic simulation…' : 'Run backtest'}</button
						>
					</div>
				</section>{/if}
		{/if}
	</main>
</div>

<style>
	.strategy-form {
		margin-bottom: 18px;
		padding-bottom: 22px;
	}
	.form-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 16px;
		padding: 22px 24px;
	}
	label {
		display: grid;
		gap: 7px;
		color: #aeb9bb;
		font-size: 12px;
	}
	input {
		width: 100%;
		border: 1px solid #303a3c;
		border-radius: 8px;
		background: #101617;
		color: #edf3f3;
		padding: 10px 11px;
		font: inherit;
	}
	.action-row {
		display: flex;
		align-items: center;
		gap: 16px;
		padding: 0 24px;
		flex-wrap: wrap;
	}
	.action-row code {
		font-size: 11px;
		word-break: break-all;
	}
	@media (max-width: 700px) {
		.form-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
