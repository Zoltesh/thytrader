<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import {
		archivePublishedStrategy,
		createDraft,
		clonePublishedStrategy,
		fetchDraftVersion,
		fetchStrategySource,
		importStrategy,
		listStrategies,
		toBuilderModel,
		type BuilderModel,
		type StrategyLibraryEntry
	} from '$lib/strategies';
	import { plainEnglishSummary, validateDefinition, ENGINE_SUPPORT } from '$lib/strategy-insight';

	let entries = $state<StrategyLibraryEntry[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let pendingAction = $state<string | null>(null);
	let showImport = $state(false);
	let importText = $state('');
	let importHint = $state<string | null>(null);
	let viewEntry = $state<StrategyLibraryEntry | null>(null);
	let viewModel = $state<BuilderModel | null>(null);
	let viewLoading = $state(false);
	let viewError = $state<string | null>(null);
	let hoveredId = $state<string | null>(null);
	let barPosition = $state<{ x: number; y: number } | null>(null);
	let barWidth = $state(0);
	let barHeight = $state(0);
	let hideTimer: ReturnType<typeof setTimeout> | null = null;

	function showBar(event: MouseEvent, entry: StrategyLibraryEntry): void {
		cancelHide();
		hoveredId = entry.strategy_id;
		updateBarPosition(event);
	}

	function updateBarPosition(event: MouseEvent): void {
		const gap = 14;
		const margin = 8;
		const height = barHeight || 46;
		const width = barWidth || 240;
		const rightEdge = window.innerWidth - margin;
		let x = event.clientX + gap;
		// Prefer the right of the cursor; flip to the left when it would clip.
		if (x + width > rightEdge) {
			x = event.clientX - gap - width;
		}
		// Cursor hugging the edge in both directions: clamp inside the viewport.
		x = Math.min(Math.max(x, margin), rightEdge - width);
		let y = event.clientY - height - gap;
		if (y < margin) {
			// Not enough room above the cursor: drop the bar below it.
			y = event.clientY + gap;
		}
		barPosition = { x, y };
	}

	function scheduleHide(): void {
		cancelHide();
		hideTimer = setTimeout(() => {
			hoveredId = null;
			barPosition = null;
		}, 140);
	}

	function cancelHide(): void {
		if (hideTimer !== null) {
			clearTimeout(hideTimer);
			hideTimer = null;
		}
	}

	async function openView(entry: StrategyLibraryEntry): Promise<void> {
		viewEntry = entry;
		viewModel = null;
		viewError = null;
		viewLoading = true;
		try {
			if (entry.status === 'draft') {
				const draft = await fetchDraftVersion(entry.strategy_id, 1);
				viewModel = toBuilderModel(draft.strategy, draft.revision);
			} else if (entry.latest_fingerprint) {
				const source = await fetchStrategySource(entry.latest_fingerprint);
				viewModel = toBuilderModel(source, 0);
			} else {
				viewError = 'No immutable evidence is available for this strategy.';
			}
		} catch (caught) {
			viewError = caught instanceof Error ? caught.message : 'Could not load strategy details.';
		} finally {
			viewLoading = false;
		}
	}

	function closeView(): void {
		viewEntry = null;
		viewModel = null;
		viewError = null;
	}

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
		<section class="top-actions" aria-label="Library actions">
			<button class="refresh" type="button" onclick={createNew} disabled={pendingAction !== null}
				>{pendingAction === 'create' ? 'Creating…' : 'New strategy'}</button
			>
			<button class="secondary" type="button" onclick={openImport}>Import JSON…</button>
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
						Use <strong>New strategy</strong> above to create a conservative reference draft, or import
						a strategy definition you exported elsewhere.
					</p>
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
							</tr>
						</thead>
						<tbody>
							{#each entries as entry (entry.strategy_id)}
								<tr
									class:hover-row={hoveredId === entry.strategy_id}
									onmouseenter={(event) => showBar(event, entry)}
									onmousemove={(event) => {
										if (hoveredId === entry.strategy_id) updateBarPosition(event);
									}}
									onmouseleave={scheduleHide}
									onclick={(event) => {
										if ((event.target as HTMLElement).closest('a, button')) return;
										openView(entry);
									}}
								>
									<td>
										<span class="strategy-name">{entry.name}</span>
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
								</tr>
							{/each}
						</tbody>
					</table>
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

{#if viewEntry}
	<div class="import-backdrop">
		<div class="view-drawer" role="dialog" aria-modal="true" aria-label="Strategy inspector">
			<div class="view-head">
				<div>
					<p class="eyebrow">{viewEntry.status} · v{viewEntry.latest_version ?? '—'}</p>
					<h2>{viewEntry.name}</h2>
				</div>
				<button class="secondary" type="button" onclick={closeView}>Close</button>
			</div>
			{#if viewLoading}
				<p class="view-note">Loading strategy evidence…</p>
			{:else if viewError}
				<p class="view-problem" role="alert">{viewError}</p>
			{:else if viewModel}
				<div class="view-block">
					<h3>Plain-English summary</h3>
					<p>{plainEnglishSummary(viewModel)}</p>
				</div>
				<div class="view-block">
					<h3>Validation</h3>
					{#if validateDefinition(viewModel).length === 0}
						<p class="view-ok">No problems detected.</p>
					{:else}
						<ul class="view-problems">
							{#each validateDefinition(viewModel) as problem (problem)}
								<li>{problem}</li>
							{/each}
						</ul>
					{/if}
				</div>
				<div class="view-block">
					<h3>Required data</h3>
					<p>
						{viewModel.warmup_bars} completed {viewModel.timeframe} bars (OHLCV) before the first signal.
					</p>
				</div>
				<div class="view-block">
					<h3>Engine support (thytrader-bar-backtest-v1)</h3>
					<ul class="engine-list">
						{#each ENGINE_SUPPORT as row (row.label)}
							<li class={row.supported ? 'supported' : 'unsupported'}>
								<span class="mark">{row.supported ? '✓' : '✗'}</span>
								<span>{row.label}<small>{row.note}</small></span>
							</li>
						{/each}
					</ul>
				</div>
				{#if viewEntry.status === 'draft' && viewEntry.strategy_id}
					<a class="secondary view-edit" href={resolve(`/strategies/${viewEntry.strategy_id}`)}
						>Edit this draft</a
					>
				{/if}
			{/if}
		</div>
	</div>
{/if}

{#if hoveredId !== null && barPosition !== null}
	<div
		class="hover-actions"
		bind:clientWidth={barWidth}
		bind:clientHeight={barHeight}
		style="left: {barPosition.x}px; top: {barPosition.y}px;"
		onmouseenter={cancelHide}
		onmouseleave={scheduleHide}
		role="toolbar"
		aria-label="Row actions"
		tabindex="-1"
	>
		{#if hoveredId !== null}
			{@const entry = entries.find((candidate) => candidate.strategy_id === hoveredId)}
			{#if entry}
				<button class="bar-button" type="button" onclick={() => openView(entry)}>View</button>
				{#if entry.status === 'draft'}
					<a class="bar-button" href={resolve(`/strategies/${entry.strategy_id}`)}>Edit</a>
				{/if}
				{#if entry.latest_fingerprint}
					<button
						class="bar-button"
						type="button"
						disabled={pendingAction !== null}
						onclick={() => clone(entry)}
					>
						{pendingAction === `clone:${entry.strategy_id}` ? 'Cloning…' : 'Clone'}
					</button>
					<button
						class="bar-button bar-danger"
						type="button"
						disabled={pendingAction !== null}
						onclick={() => archive(entry)}
					>
						{pendingAction === `archive:${entry.strategy_id}` ? 'Archiving…' : 'Archive'}
					</button>
				{/if}
			{/if}
		{/if}
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
	tbody tr {
		cursor: default;
	}
	tbody tr.hover-row td {
		background: #1a2426;
	}
	tbody tr.hover-row .strategy-name {
		color: #9fe0bd;
	}
	.strategy-name {
		display: block;
		color: #edf3f3;
		font-weight: 600;
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
	.hover-actions {
		position: fixed;
		z-index: 30;
		display: flex;
		gap: 6px;
		padding: 6px;
		background: #1d2627;
		border: 1px solid #3a4648;
		border-radius: 10px;
		box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45);
		animation: bar-in 90ms ease-out;
	}
	@keyframes bar-in {
		from {
			opacity: 0;
			transform: translateY(3px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}
	.bar-button {
		border: 1px solid #455457;
		border-radius: 8px;
		background: transparent;
		color: #d8e1e2;
		padding: 6px 11px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
		text-decoration: none;
		display: inline-block;
	}
	.bar-button:hover:not(:disabled) {
		background: #273437;
		border-color: #5b6c70;
	}
	.bar-button.bar-danger:hover:not(:disabled) {
		color: #f0a3a3;
		border-color: #6c4040;
	}
	.bar-button:disabled {
		opacity: 0.55;
		cursor: default;
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
	.top-actions {
		display: flex;
		gap: 12px;
		margin-bottom: 16px;
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
	.view-drawer {
		width: min(640px, 100%);
		max-height: 86vh;
		overflow-y: auto;
		background: #141b1c;
		border: 1px solid #303a3c;
		border-radius: 12px;
		padding: 20px 24px;
		display: grid;
		gap: 16px;
	}
	.view-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 12px;
	}
	.view-head h2 {
		margin: 2px 0 0;
		font-size: 18px;
		color: #edf3f3;
	}
	.view-block h3 {
		margin: 0 0 6px;
		font-size: 12px;
		color: #aeb9bb;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.view-block p {
		margin: 0;
		font-size: 13px;
		color: #d8e1e2;
	}
	.view-note {
		color: #aeb9bb;
		font-size: 13px;
	}
	.view-ok {
		color: #83d5a3;
		font-size: 13px;
	}
	.view-problem {
		color: #f0a3a3;
		font-size: 13px;
	}
	.view-problems {
		margin: 0;
		padding-left: 16px;
		color: #f0a3a3;
		font-size: 12px;
		display: grid;
		gap: 4px;
	}
	.view-edit {
		justify-self: start;
		text-decoration: none;
	}
	.engine-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 8px;
	}
	.engine-list li {
		display: flex;
		gap: 8px;
		font-size: 12px;
		color: #d8e1e2;
	}
	.engine-list li .mark {
		font-weight: 700;
	}
	.engine-list li.supported .mark {
		color: #83d5a3;
	}
	.engine-list li.unsupported {
		color: #9aa8aa;
	}
	.engine-list li.unsupported .mark {
		color: #f0a3a3;
	}
	.engine-list small {
		display: block;
		color: #77888b;
	}
	@media (max-width: 900px) {
		table {
			font-size: 12px;
		}
	}
</style>
