<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import {
		archivePublishedStrategy,
		createDraft,
		listDatasets,
		listDrafts,
		listPublishedStrategies,
		publishDraft,
		saveDraft,
		submitBacktest,
		type Dataset,
		type StrategyDraft
	} from '$lib/strategies';

	let draft: StrategyDraft | null = $state(null);
	let draftRevision = $state(0);
	let draftSummary = $state<string | null>(null);
	let draftSaved = $state(false);
	let publishedFingerprint = $state<string | null>(null);
	let archived = $state(false);
	let datasets = $state<Dataset[]>([]);
	let datasetFingerprint = $state('');
	let evaluationStart = $state('');
	let evaluationEnd = $state('');
	let error = $state<string | null>(null);
	let loading = $state(true);
	let publishing = $state(false);
	let mutationPending = $state(false);
	let submitting = $state(false);

	async function loadWorkspace(): Promise<void> {
		loading = true;
		error = null;
		draft = null;
		draftRevision = 0;
		draftSummary = null;
		draftSaved = false;
		publishedFingerprint = null;
		archived = false;
		try {
			const [savedDrafts, publications, datasetsOutcome] = await Promise.all([
				listDrafts(),
				listPublishedStrategies(),
				listDatasets().catch((caught: unknown): Dataset[] | Error =>
					caught instanceof Error ? caught : new Error('Could not load verified datasets.')
				)
			]);
			const recovered = [...savedDrafts, ...publications].sort(
				(left, right) =>
					Date.parse(right.strategy.created_at) - Date.parse(left.strategy.created_at) ||
					Number(right.strategy.status === 'published') -
						Number(left.strategy.status === 'published')
			)[0];
			if (recovered?.revision !== null && recovered?.strategy.status === 'draft') {
				draft = recovered.strategy;
				draftRevision = recovered.revision;
				draftSummary = recovered.summary;
				draftSaved = true;
				publishedFingerprint = null;
				archived = false;
			} else if (recovered?.strategy_fingerprint) {
				draft = recovered.strategy;
				draftRevision = 0;
				draftSummary = recovered.summary;
				draftSaved = false;
				publishedFingerprint = recovered.strategy_fingerprint;
				archived = false;
			} else {
				await startNewDraft();
			}

			if (datasetsOutcome instanceof Error) {
				error = datasetsOutcome.message;
			} else {
				datasets = datasetsOutcome.filter((dataset) => dataset.product_id === 'BTC-USD');
				datasetFingerprint = datasets[0]?.content_fingerprint ?? '';
			}
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not load strategy drafts.';
		} finally {
			loading = false;
		}
	}

	async function startNewDraft(): Promise<void> {
		if (mutationPending) return;
		mutationPending = true;
		loading = true;
		error = null;
		try {
			const created = await createDraft();
			draft = created.strategy;
			draftRevision = created.revision;
			draftSummary = created.summary;
			draftSaved = true;
			publishedFingerprint = null;
			archived = false;
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not create a strategy draft.';
		} finally {
			loading = false;
			mutationPending = false;
		}
	}

	function markDraftDirty(): void {
		draftSaved = false;
		draftSummary = null;
	}

	async function save(): Promise<void> {
		if (!draft || mutationPending) return;
		mutationPending = true;
		draftSaved = false;
		error = null;
		try {
			const saved = await saveDraft(draft, draftRevision);
			draft = saved.strategy;
			draftRevision = saved.revision;
			draftSummary = saved.summary;
			draftSaved = true;
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not save the strategy draft.';
		} finally {
			mutationPending = false;
		}
	}

	async function publish(): Promise<void> {
		if (!draft || mutationPending) return;
		mutationPending = true;
		publishing = true;
		error = null;
		try {
			const published = await publishDraft(draft, draftRevision);
			publishedFingerprint = published.strategy_fingerprint;
			archived = false;
			draft = published.strategy;
			draftRevision = 0;
			draftSaved = false;
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Strategy publication failed.';
		} finally {
			publishing = false;
			mutationPending = false;
		}
	}

	async function archivePublished(): Promise<void> {
		if (!publishedFingerprint || mutationPending) return;
		mutationPending = true;
		error = null;
		try {
			await archivePublishedStrategy(publishedFingerprint);
			archived = true;
		} catch (caught) {
			error =
				caught instanceof Error ? caught.message : 'Could not archive the published strategy.';
		} finally {
			mutationPending = false;
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

	onMount(() => void loadWorkspace());
</script>

<svelte:head><title>Strategies · ThyTrader</title></svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home"
			><span class="brand-mark">T</span><span>ThyTrader</span></a
		>
		<nav aria-label="Primary navigation">
			<a href={resolve('/')}>Portfolio</a>
			<a class="active" href={resolve('/strategies')}>Strategies</a>
			<a href={resolve('/backtests')}>Backtests</a>
			<a href={resolve('/audit')}>Audit</a>
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
				<button type="button" onclick={loadWorkspace}>Retry workspace load</button>
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
				{#if draftSummary}<p class="strategy-summary">{draftSummary}</p>{/if}
				<div class="form-grid">
					<label
						>Strategy name<input
							bind:value={draft.name}
							oninput={markDraftDirty}
							disabled={draft.status !== 'draft' || mutationPending}
						/></label
					>
					<label
						>Risk per trade<input
							inputmode="decimal"
							bind:value={draft.sizing.risk_fraction}
							oninput={markDraftDirty}
							disabled={draft.status !== 'draft' || mutationPending}
						/></label
					>
					<label
						>Minimum USD notional<input
							inputmode="decimal"
							bind:value={draft.sizing.min_quote_notional}
							oninput={markDraftDirty}
							disabled={draft.status !== 'draft' || mutationPending}
						/></label
					>
					<label
						>Maximum USD notional<input
							inputmode="decimal"
							bind:value={draft.sizing.max_quote_notional}
							oninput={markDraftDirty}
							disabled={draft.status !== 'draft' || mutationPending}
						/></label
					>
				</div>
				<div class="action-row">
					<button
						class="secondary"
						type="button"
						onclick={save}
						disabled={draft.status !== 'draft' || mutationPending}>Save draft</button
					>{#if draftSaved}<span class="saved">Draft saved</span>{/if}
					<button class="secondary" type="button" onclick={startNewDraft} disabled={mutationPending}
						>New reference draft</button
					>
					<button
						class="refresh"
						type="button"
						onclick={publish}
						disabled={mutationPending || draft.status !== 'draft'}
						>{publishing ? 'Publishing…' : 'Validate & publish immutable version'}</button
					>{#if publishedFingerprint}<code>{publishedFingerprint}</code>{/if}
					>{#if publishedFingerprint && !archived}<button
							class="secondary"
							type="button"
							onclick={archivePublished}
							disabled={mutationPending}>Archive published strategy</button
						>{:else if archived}<span class="saved">Published strategy archived</span>{/if}
				</div>
			</section>
			{#if publishedFingerprint && !archived}<section class="asset-panel strategy-form">
					<div class="panel-heading">
						<div>
							<h2>Run historical backtest</h2>
							<p>Use a verified BTC-USD 1h dataset fingerprint and explicit evaluation window.</p>
						</div>
					</div>
					<div class="form-grid">
						<label
							>Verified BTC-USD 1h dataset<select bind:value={datasetFingerprint}>
								<option value="">Select a verified dataset</option>
								{#each datasets as dataset (dataset.content_fingerprint)}
									<option value={dataset.content_fingerprint}
										>{new Date(dataset.starts_at).toLocaleDateString()} – {new Date(
											dataset.ends_at
										).toLocaleDateString()} · {dataset.content_fingerprint.slice(0, 18)}…</option
									>
								{/each}
							</select></label
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
	.strategy-summary {
		margin: 0;
		padding: 0 24px 4px;
		color: #aeb9bb;
		font-size: 13px;
	}
	.secondary {
		border: 1px solid #455457;
		border-radius: 8px;
		background: transparent;
		color: #d8e1e2;
		padding: 10px 12px;
		font: inherit;
		cursor: pointer;
	}
	.saved {
		color: #83d5a3;
		font-size: 12px;
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
	input,
	select {
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
