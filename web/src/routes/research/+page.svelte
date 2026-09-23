<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import ResearchLaunchPanel from '$lib/ResearchLaunchPanel.svelte';
	import {
		fetchDraftVersion,
		fetchStrategySource,
		listStrategies,
		toBuilderModel,
		type BuilderModel,
		type StrategyLibraryEntry
	} from '$lib/strategies';

	let entries = $state<StrategyLibraryEntry[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let model = $state<BuilderModel | null>(null);
	let modelError = $state<string | null>(null);
	let modelLoading = $state(false);

	const selectedId = $derived(page.url.searchParams.get('strategy') ?? '');
	/**
	 * Exact published fingerprint requested by the caller, or '' when absent.
	 *
	 * Passed through to the launch panel so backtests run against the linked
	 * version, not whatever version happens to be newest.
	 */
	const requestedFingerprint = $derived(page.url.searchParams.get('strategy_fingerprint') ?? '');
	const selected = $derived(entries.find((entry) => entry.strategy_id === selectedId) ?? null);

	async function loadLibrary(): Promise<void> {
		loading = true;
		error = null;
		try {
			entries = await listStrategies();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not load the strategy library.';
		} finally {
			loading = false;
		}
	}

	async function loadModel(entry: StrategyLibraryEntry): Promise<void> {
		modelLoading = true;
		modelError = null;
		model = null;
		try {
			if (entry.status === 'draft') {
				const draftVersion = entry.latest_version ?? 1;
				const draft = await fetchDraftVersion(entry.strategy_id, draftVersion);
				model = toBuilderModel(draft.strategy, draft.revision);
			} else if (entry.latest_fingerprint) {
				const source = await fetchStrategySource(entry.latest_fingerprint);
				model = toBuilderModel(source, 0);
			} else {
				modelError = 'No immutable evidence is available for this strategy.';
			}
		} catch (caught) {
			modelError = caught instanceof Error ? caught.message : 'Could not load strategy details.';
		} finally {
			modelLoading = false;
		}
	}

	function selectStrategy(strategyId: string): void {
		void goto(resolve(`/research?strategy=${encodeURIComponent(strategyId)}`));
	}

	$effect(() => {
		if (selected === null) {
			model = null;
			modelError = null;
			return;
		}
		void loadModel(selected);
	});

	onMount(() => {
		void loadLibrary();
	});
</script>

<svelte:head>
	<title>Research · ThyTrader</title>
</svelte:head>

<main class="workstation-page">
	<section class="hero">
		<div>
			<p class="eyebrow">Conservative research</p>
			<h1>Research</h1>
			<p class="lede">
				Launch a deterministic backtest or composed study against an immutable published strategy.
				Paper and live stay on Deploy. Confirmation-gated agents use
				<code>thytrader-research --confirm</code>.
			</p>
		</div>
	</section>
	{#if error}
		<div class="error-banner" role="alert">
			<strong>Research operation unavailable</strong>
			<p>{error}</p>
		</div>
	{/if}
	<label class="picker"
		>Strategy
		<select
			value={selectedId}
			onchange={(event) => selectStrategy((event.currentTarget as HTMLSelectElement).value)}
			disabled={loading}
		>
			<option value="">Select a strategy</option>
			{#each entries as entry (entry.strategy_id)}
				<option value={entry.strategy_id}
					>{entry.name} · {entry.product_id} · {entry.timeframe}</option
				>
			{/each}
		</select>
	</label>
	{#if selectedId === ''}
		<p class="empty-hint">Choose a strategy to launch research. Create drafts from Strategies.</p>
	{:else if modelLoading}
		<p class="empty-hint">Loading strategy evidence…</p>
	{:else if modelError}
		<p class="error-banner" role="alert">{modelError}</p>
	{:else if selected && model}
		<p class="strategy-meta">
			{selected.name} · {selected.status} · v{selected.latest_version ?? '—'}
		</p>
		<ResearchLaunchPanel entry={selected} {model} {requestedFingerprint} />
	{/if}
</main>

<style>
	.workstation-page {
		width: min(1400px, 94vw);
	}
	.picker,
	.picker select {
		display: grid;
		gap: 6px;
		max-width: 520px;
		margin-bottom: 24px;
	}
	.picker {
		color: #aeb9bb;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.picker select {
		text-transform: none;
		letter-spacing: 0;
		font-size: 14px;
		color: #edf3f3;
		background: #101617;
		border: 1px solid #303a3c;
		border-radius: 8px;
		padding: 10px 12px;
	}
	.strategy-meta {
		color: #8d999c;
		font-size: 13px;
		margin: 0 0 20px;
	}
	.empty-hint {
		color: #8d999c;
	}
	.error-banner {
		color: #f0a3a3;
		margin-bottom: 16px;
	}
</style>
