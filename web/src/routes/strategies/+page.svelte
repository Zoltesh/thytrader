<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import {
		archivePublishedStrategy,
		createDraft,
		clonePublishedStrategy,
		importStrategy,
		listStrategies,
		type StrategyLibraryEntry
	} from '$lib/strategies';

	let entries = $state<StrategyLibraryEntry[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let pendingAction = $state<string | null>(null);
	let showImport = $state(false);
	let importText = $state('');
	let importHint = $state<string | null>(null);

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

	async function createNew(): Promise<void> {
		if (pendingAction) return;
		pendingAction = 'create';
		error = null;
		try {
			await createDraft();
			await loadLibrary();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not create a strategy draft.';
		} finally {
			pendingAction = null;
		}
	}

	async function clone(entry: StrategyLibraryEntry): Promise<void> {
		if (pendingAction || !entry.latest_fingerprint) return;
		pendingAction = `clone:${entry.strategy_id}`;
		error = null;
		try {
			await clonePublishedStrategy(entry.latest_fingerprint);
			await loadLibrary();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not clone the strategy.';
		} finally {
			pendingAction = null;
		}
	}

	async function archive(entry: StrategyLibraryEntry): Promise<void> {
		if (pendingAction || !entry.latest_fingerprint) return;
		pendingAction = `archive:${entry.strategy_id}`;
		error = null;
		try {
			await archivePublishedStrategy(entry.latest_fingerprint);
			await loadLibrary();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not archive the strategy.';
		} finally {
			pendingAction = null;
		}
	}

	function openImport(): void {
		importHint = null;
		importText = '';
		showImport = true;
	}

	function closeImport(): void {
		showImport = false;
		importText = '';
		importHint = null;
	}

	async function runImport(): Promise<void> {
		if (pendingAction) return;
		pendingAction = 'import';
		importHint = null;
		error = null;
		try {
			const parsed: unknown = JSON.parse(importText);
			await importStrategy(parsed);
			closeImport();
			await loadLibrary();
		} catch (caught) {
			if (caught instanceof SyntaxError) {
				importHint = 'That is not valid JSON. Paste a complete strategy definition.';
			} else {
				importHint =
					caught instanceof Error ? caught.message : 'Could not import the strategy definition.';
			}
		} finally {
			pendingAction = null;
		}
	}

	function formatReturn(entry: StrategyLibraryEntry): string {
		if (!entry.backtest) return '—';
		const fraction = Number(entry.backtest.summary.total_return_fraction);
		if (Number.isNaN(fraction)) return '—';
		return `${(fraction * 100).toFixed(2)}%`;
	}

	function formatDate(value: string): string {
		return new Date(value).toLocaleDateString();
	}

	onMount(() => void loadLibrary());
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
				<h1>Strategy library</h1>
				<p class="lede">
					Every strategy identity with its latest evidence. Nothing here deploys or trades.
				</p>
			</div>
		</section>
		{#if error}<div class="error-banner" role="alert">
				<div>
					<strong>Research operation unavailable</strong>
					<p>{error}</p>
				</div>
				<button type="button" onclick={loadLibrary}>Retry library load</button>
			</div>{/if}
		<section class="library-card" aria-label="Strategy library">
			{#if loading}
				<div class="loading-region"><div class="skeleton wide"></div></div>
			{:else if entries.length === 0}
				<div class="empty-state">
					<p>No strategies yet.</p>
					<p class="empty-hint">
						Create a conservative reference draft, or import a strategy definition you exported
						elsewhere.
					</p>
					<div class="empty-actions">
						<button
							class="refresh"
							type="button"
							onclick={createNew}
							disabled={pendingAction !== null}
							>{pendingAction === 'create' ? 'Creating…' : 'Create reference strategy'}</button
						>
						<button class="secondary" type="button" onclick={openImport}>Import JSON…</button>
					</div>
				</div>
			{:else}
				<div class="table-scroll">
					<table>
						<thead>
							<tr>
								<th scope="col">Name</th>
								<th scope="col">Market / timeframe</th>
								<th scope="col">Latest version</th>
								<th scope="col">Status</th>
								<th scope="col">Latest backtest</th>
								<th scope="col">Paper / live</th>
								<th scope="col">Actions</th>
							</tr>
						</thead>
						<tbody>
							{#each entries as entry (entry.strategy_id)}
								<tr>
									<td>
										<span class="strategy-name">{entry.name}</span>
										<span class="strategy-summary">{entry.summary}</span>
									</td>
									<td>{entry.product_id} · {entry.timeframe}</td>
									<td>
										{entry.latest_version ?? '—'}{#if entry.latest_fingerprint}
											<code class="fingerprint">{entry.latest_fingerprint.slice(0, 18)}…</code>{/if}
									</td>
									<td>
										<span class="status-pill" data-status={entry.status}>{entry.status}</span>
									</td>
									<td>
										{#if entry.backtest}
											<a
												href={resolve(
													`/backtests?result=${encodeURIComponent(entry.backtest.result_fingerprint)}`
												)}
											>
												{formatReturn(entry)} · {entry.backtest.summary.trade_count} trades ·
												{formatDate(entry.backtest.published_at)}
											</a>
										{:else}
											<span class="muted">None yet</span>
										{/if}
									</td>
									<td>
										<span class="muted">{entry.paper_live.paper} / {entry.paper_live.live}</span>
									</td>
									<td>
										<div class="row-actions">
											{#if entry.status === 'draft'}
												<a class="secondary" href={resolve(`/strategies/${entry.strategy_id}`)}
													>Edit</a
												>
											{/if}
											<button
												class="secondary"
												type="button"
												onclick={createNew}
												disabled={pendingAction !== null}>New</button
											>
											{#if entry.latest_fingerprint}
												<button
													class="secondary"
													type="button"
													onclick={() => clone(entry)}
													disabled={pendingAction !== null}
													>{pendingAction === `clone:${entry.strategy_id}`
														? 'Cloning…'
														: 'Clone'}</button
												>
												<button
													class="secondary"
													type="button"
													onclick={() => archive(entry)}
													disabled={pendingAction !== null}
													>{pendingAction === `archive:${entry.strategy_id}`
														? 'Archiving…'
														: 'Archive'}</button
												>
											{/if}
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<div class="library-footer">
					<button
						class="refresh"
						type="button"
						onclick={createNew}
						disabled={pendingAction !== null}
						>{pendingAction === 'create' ? 'Creating…' : 'Create reference strategy'}</button
					>
					<button class="secondary" type="button" onclick={openImport}>Import JSON…</button>
				</div>
			{/if}
		</section>
	</main>
</div>

{#if showImport}
	<div class="import-backdrop">
		<div class="import-dialog" role="dialog" aria-modal="true" aria-label="Import strategy JSON">
			<h2>Import strategy JSON</h2>
			<p class="import-lede">
				Paste one complete strategy definition. It is stored as a new editable draft.
			</p>
			<textarea
				bind:value={importText}
				rows={12}
				spellcheck="false"
				aria-label="Strategy definition JSON"></textarea>
			{#if importHint}<p class="import-hint" role="alert">{importHint}</p>{/if}
			<div class="import-actions">
				<button
					class="secondary"
					type="button"
					onclick={closeImport}
					disabled={pendingAction !== null}>Cancel</button
				>
				<button
					class="refresh"
					type="button"
					onclick={runImport}
					disabled={pendingAction !== null || importText.trim().length === 0}
					>{pendingAction === 'import' ? 'Importing…' : 'Import draft'}</button
				>
			</div>
		</div>
	</div>
{/if}

<style>
	.library-card {
		background: var(--card, #141b1c);
		border: 1px solid #303a3c;
		border-radius: 12px;
		overflow: hidden;
		margin-bottom: 24px;
	}
	.loading-region {
		padding: 24px;
	}
	.table-scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}
	th,
	td {
		text-align: left;
		padding: 14px 16px;
		border-bottom: 1px solid #232d2e;
		vertical-align: top;
	}
	th {
		color: #aeb9bb;
		font-weight: 500;
		font-size: 12px;
		white-space: nowrap;
	}
	tbody tr:last-child td {
		border-bottom: none;
	}
	.strategy-name {
		display: block;
		color: #edf3f3;
		font-weight: 600;
	}
	.strategy-summary {
		display: block;
		color: #aeb9bb;
		font-size: 12px;
		margin-top: 4px;
	}
	.fingerprint {
		display: block;
		font-size: 10px;
		color: #77888b;
		margin-top: 3px;
		word-break: break-all;
	}
	.status-pill {
		display: inline-block;
		border-radius: 999px;
		padding: 3px 10px;
		font-size: 11px;
		background: #1d2627;
		color: #d8e1e2;
		border: 1px solid #3a4648;
	}
	.status-pill[data-status='published'] {
		color: #83d5a3;
		border-color: #2f5c44;
	}
	.status-pill[data-status='archived'] {
		color: #9aa8aa;
		border-color: #38444a;
	}
	.muted {
		color: #77888b;
	}
	td a {
		color: #7fd0f0;
	}
	.row-actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.row-actions a.secondary {
		display: inline-block;
		text-decoration: none;
	}
	.secondary {
		border: 1px solid #455457;
		border-radius: 8px;
		background: transparent;
		color: #d8e1e2;
		padding: 8px 10px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
	}
	.refresh {
		border: none;
		border-radius: 8px;
		background: #2f6f52;
		color: #eafff3;
		padding: 10px 14px;
		font: inherit;
		font-size: 13px;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.55;
		cursor: default;
	}
	.library-footer {
		display: flex;
		gap: 12px;
		padding: 14px 16px;
		border-top: 1px solid #232d2e;
	}
	.empty-state {
		padding: 42px 24px;
		text-align: center;
		color: #d8e1e2;
	}
	.empty-state p {
		margin: 0 0 8px;
	}
	.empty-hint {
		color: #aeb9bb;
		font-size: 13px;
	}
	.empty-actions {
		display: flex;
		gap: 12px;
		justify-content: center;
		margin-top: 18px;
	}
	.import-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(5, 9, 10, 0.72);
		display: grid;
		place-items: center;
		padding: 24px;
		z-index: 20;
	}
	.import-dialog {
		width: min(680px, 100%);
		background: #141b1c;
		border: 1px solid #303a3c;
		border-radius: 12px;
		padding: 22px 24px;
	}
	.import-dialog h2 {
		margin: 0 0 6px;
		font-size: 18px;
		color: #edf3f3;
	}
	.import-lede {
		margin: 0 0 14px;
		color: #aeb9bb;
		font-size: 13px;
	}
	textarea {
		width: 100%;
		border: 1px solid #303a3c;
		border-radius: 8px;
		background: #101617;
		color: #edf3f3;
		padding: 12px;
		font-family: ui-monospace, monospace;
		font-size: 12px;
		resize: vertical;
	}
	.import-hint {
		color: #f0a3a3;
		font-size: 12px;
		margin: 10px 0 0;
	}
	.import-actions {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		margin-top: 14px;
	}
	@media (max-width: 900px) {
		table {
			font-size: 12px;
		}
	}
</style>
