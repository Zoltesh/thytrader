<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import DeployWorkstation from '$lib/DeployWorkstation.svelte';
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

	const selectedId = $derived(page.url.searchParams.get('strategy') ?? '');
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
		model = null;
		try {
			if (entry.status === 'draft') {
				const draft = await fetchDraftVersion(entry.strategy_id, entry.latest_version ?? 1);
				model = toBuilderModel(draft.strategy, draft.revision);
			} else if (entry.latest_fingerprint) {
				const source = await fetchStrategySource(entry.latest_fingerprint);
				model = toBuilderModel(source, 0);
			}
		} catch {
			model = null;
		}
	}

	function selectStrategy(strategyId: string): void {
		void goto(resolve(`/deploy?strategy=${encodeURIComponent(strategyId)}`));
	}

	$effect(() => {
		if (selected === null) {
			model = null;
			return;
		}
		void loadModel(selected);
	});

	onMount(() => {
		void loadLibrary();
	});
</script>

<svelte:head>
	<title>Deploy · ThyTrader</title>
</svelte:head>

<main class="workstation-page">
	<section class="hero">
		<div>
			<p class="eyebrow">Paper and live</p>
			<h1>Deploy</h1>
			<p class="lede">
				Start, pause, resume, or stop a published strategy. Live places real Coinbase spot orders.
				Agents use <code>thytrader-runtime --confirm</code>; live also
				<code>--i-understand-live</code>.
			</p>
		</div>
	</section>
	{#if error}
		<div class="error-banner" role="alert">{error}</div>
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
		<p class="empty-hint">Choose a published strategy. Discretionary orders stay on Trade.</p>
	{:else if selected}
		<p class="strategy-meta">
			{selected.name} · {selected.status} · v{selected.latest_version ?? '—'}
		</p>
		<DeployWorkstation entry={selected} {model} onChanged={() => void loadLibrary()} />
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
