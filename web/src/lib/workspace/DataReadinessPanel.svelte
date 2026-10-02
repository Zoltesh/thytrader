<script lang="ts">
	/**
	 * Names every product × timeframe dataset the strategy needs that is
	 * missing or stale, and offers a confirmation-gated "Download data" action:
	 * watch-add (keeping a longer existing lookback) plus a no-wait ingest
	 * through the data-lane HTTP API. Progress polls the worker state until the
	 * watch window is covered, then asks the parent to reload datasets.
	 *
	 * An HTF filter or extra indicator clock also links to Build, where the
	 * clock can be removed or changed instead of downloaded.
	 */
	import { resolve } from '$app/paths';
	import { onDestroy } from 'svelte';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import {
		clockLabel,
		defaultWatchLookbackHours,
		describeLookbackHours,
		downloadProgress,
		fetchIngestStatus,
		readinessMessage,
		requestDatasetDownload,
		strategyReadiness,
		type ClockReadiness,
		type DownloadProgress
	} from '$lib/data-readiness';
	import type { BuilderModel, Dataset } from '$lib/strategies';
	import { workspaceHref } from '$lib/strategy-workspace';

	let {
		strategyId,
		model,
		datasets,
		context,
		onRefresh,
		pollMs = 5000
	}: {
		strategyId: string;
		model: BuilderModel;
		/** Latest verified datasets (any product); null while unknown. */
		datasets: Dataset[] | null;
		/** `test` blocks a backtest; `run` explains what paper/live read. */
		context: 'test' | 'run';
		/** Reload the verified dataset catalog after a download completes. */
		onRefresh: () => void;
		/** Progress poll interval (tests shorten it). */
		pollMs?: number;
	} = $props();

	const problems = $derived(
		datasets === null
			? []
			: strategyReadiness(model, datasets).filter((item) => item.availability !== 'ready')
	);

	let confirmTarget = $state<ClockReadiness | null>(null);
	let submitting = $state(false);
	let submitError = $state<string | null>(null);
	let progress = $state<Record<string, DownloadProgress>>({});
	let pollErrors = $state<Record<string, string>>({});
	/** Pending poll timers by clock; not reactive state (never rendered). */
	const timers: Record<string, ReturnType<typeof setTimeout>> = {};

	function key(item: { productId: string; timeframe: string }): string {
		return `${item.productId}:${item.timeframe}`;
	}

	async function confirmDownload(): Promise<void> {
		const target = confirmTarget;
		if (target === null || submitting) return;
		submitting = true;
		submitError = null;
		try {
			const accepted = await requestDatasetDownload(target.productId, target.timeframe);
			progress = { ...progress, [key(target)]: downloadProgress(accepted) };
			confirmTarget = null;
			schedulePoll(target.productId, target.timeframe);
		} catch (caught) {
			submitError = caught instanceof Error ? caught.message : 'The download was not queued.';
		} finally {
			submitting = false;
		}
	}

	function schedulePoll(productId: string, timeframe: string): void {
		const id = `${productId}:${timeframe}`;
		const existing = timers[id];
		if (existing !== undefined) clearTimeout(existing);
		timers[id] = setTimeout(() => void poll(productId, timeframe), pollMs);
	}

	async function poll(productId: string, timeframe: string): Promise<void> {
		const id = `${productId}:${timeframe}`;
		delete timers[id];
		try {
			const status = await fetchIngestStatus(productId, timeframe);
			const next = downloadProgress(status);
			progress = { ...progress, [id]: next };
			pollErrors = Object.fromEntries(
				Object.entries(pollErrors).filter(([errorId]) => errorId !== id)
			);
			if (next.done) {
				onRefresh();
				return;
			}
		} catch (caught) {
			pollErrors = {
				...pollErrors,
				[id]: caught instanceof Error ? caught.message : 'Progress is unavailable.'
			};
		}
		schedulePoll(productId, timeframe);
	}

	onDestroy(() => {
		for (const [id, timer] of Object.entries(timers)) {
			clearTimeout(timer);
			delete timers[id];
		}
	});
</script>

{#if problems.length > 0 || Object.keys(progress).length > 0}
	<section class="readiness" aria-label="Required market data" data-testid="data-readiness">
		<h3>Required market data</h3>
		<p class="intro">
			{context === 'test'
				? 'A backtest needs a verified dataset for every clock this strategy reads.'
				: 'Paper and live read complete-only candles for every clock this strategy reads; missing coverage pauses the bot.'}
		</p>
		<ul>
			{#each problems as item (key(item))}
				{@const state = progress[key(item)]}
				<li data-testid={`data-readiness-${item.timeframe}`}>
					<p class="what">
						<strong>{item.availability === 'missing' ? 'Missing' : 'Stale'}:</strong>
						{readinessMessage(item)}
					</p>
					<div class="row-actions">
						<button
							class="btn"
							type="button"
							disabled={state !== undefined && !state.done}
							onclick={() => {
								submitError = null;
								confirmTarget = item;
							}}>Download data…</button
						>
						{#if item.role !== 'execution'}
							<a
								class="btn"
								href={resolve(
									workspaceHref(strategyId, 'build', {
										section: item.role === 'htf' ? 'entry' : 'indicators'
									})
								)}
								>{item.role === 'htf'
									? 'Change or remove the HTF filter in Build'
									: 'Change or remove this indicator clock in Build'}</a
							>
						{/if}
					</div>
					{#if item.role === 'htf'}
						<p class="hint">
							The higher-timeframe filter is optional. Removing it removes this requirement.
						</p>
					{/if}
					{#if state}
						<p class="progress" role="status" data-testid={`data-progress-${item.timeframe}`}>
							{state.text}
						</p>
						{#if state.floorNote}<p class="hint">{state.floorNote}</p>{/if}
					{/if}
					{#if pollErrors[key(item)]}
						<p class="field-error">{pollErrors[key(item)]}</p>
					{/if}
				</li>
			{/each}
			{#each Object.entries(progress).filter(([id]) => !problems.some((item) => key(item) === id)) as [id, state] (id)}
				<li>
					<p class="progress" role="status">{id.replace(':', ' ')} · {state.text}</p>
					{#if state.floorNote}<p class="hint">{state.floorNote}</p>{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}

<ConfirmDialog
	open={confirmTarget !== null}
	title="Download market data?"
	confirmLabel="Download data"
	pendingLabel="Queuing…"
	pending={submitting}
	error={submitError}
	testId="data-download-dialog"
	oncancel={() => (confirmTarget = null)}
	onconfirm={() => void confirmDownload()}
>
	{#if confirmTarget}
		{@const lookbackHours = defaultWatchLookbackHours(confirmTarget.timeframe)}
		<p><strong>{clockLabel(confirmTarget)}</strong></p>
		<p>
			This watches {confirmTarget.productId}
			{confirmTarget.timeframe} with a {lookbackHours}-hour ({describeLookbackHours(lookbackHours)})
			lookback (an existing longer lookback is kept) and asks the market-data worker to backfill it
			now, newest bars first. Coinbase may hold less history than that; coverage then starts where
			its history does. Ingest is complete-only and never interpolates missing bars. It places no
			orders.
		</p>
	{/if}
</ConfirmDialog>

<style>
	.readiness {
		display: grid;
		gap: 8px;
		margin-top: 10px;
		padding: 12px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	h3 {
		margin: 0;
		font-size: var(--fs-md, 1rem);
	}
	.intro,
	.hint {
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	ul {
		display: grid;
		gap: 10px;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	li {
		display: grid;
		gap: 6px;
	}
	.what,
	.progress {
		margin: 0;
	}
	.row-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		align-items: center;
	}
	.field-error {
		margin: 0;
		color: var(--danger, #c0392b);
		font-size: var(--fs-sm);
	}
</style>
