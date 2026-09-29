<script lang="ts">
	/**
	 * Strategy library (ADR 0080). Each row opens that strategy's workspace
	 * and shows its evidence pipeline — Build / Test / Paper / Live chips
	 * derived from the library row payload, never a readiness verdict.
	 * Create, import, clone, and archive stay here; archive confirms in an
	 * accessible dialog. Pagination follows the server cursor.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { compareDecimalStrings, formatPercent } from '$lib/backtests';
	import { marketLabel } from '$lib/deployment-detail';
	import {
		archivePublishedStrategy,
		clonePublishedStrategy,
		createDraft,
		EXECUTION_TIMEFRAMES,
		fetchStrategyPage,
		formatUtcInputValue,
		importStrategy,
		type StrategyLibraryEntry
	} from '$lib/strategies';
	import {
		libraryPipeline,
		libraryVersionLabel,
		pipelineSummary,
		shortStrategyFingerprint,
		workspaceHref
	} from '$lib/strategy-workspace';

	let entries = $state<StrategyLibraryEntry[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let pageSize = $state<10 | 25 | 50 | 100>(10);
	let pageIndex = $state(0);
	let pageCursors = $state<(string | undefined)[]>([undefined]);
	let nextCursor = $state<string | null>(null);
	let libraryRequestId = 0;
	let pendingAction = $state<string | null>(null);
	let showImport = $state(false);
	let importText = $state('');
	let importHint = $state<string | null>(null);
	let draftTemplate = $state('ema-trend');
	let draftTimeframe = $state('1h');
	let archiveTarget = $state<StrategyLibraryEntry | null>(null);
	let archiveError = $state<string | null>(null);

	async function loadLibrary(reset = true): Promise<void> {
		const requestId = ++libraryRequestId;
		if (reset) {
			pageIndex = 0;
			pageCursors = [undefined];
		}
		loading = true;
		error = null;
		try {
			const page = await fetchStrategyPage(pageSize, pageCursors[pageIndex]);
			if (requestId !== libraryRequestId) return;
			if (page.entries.length === 0 && pageIndex > 0) {
				pageIndex -= 1;
				void loadLibrary(false);
				return;
			}
			entries = page.entries;
			nextCursor = page.nextCursor;
		} catch (caught) {
			if (requestId !== libraryRequestId) return;
			entries = [];
			nextCursor = null;
			error = caught instanceof Error ? caught.message : 'Could not load the strategy library.';
		} finally {
			if (requestId === libraryRequestId) loading = false;
		}
	}

	function changePageSize(event: Event): void {
		pageSize = Number((event.currentTarget as HTMLSelectElement).value) as typeof pageSize;
		void loadLibrary();
	}

	function nextPage(): void {
		if (loading || nextCursor === null) return;
		pageCursors = [...pageCursors.slice(0, pageIndex + 1), nextCursor];
		pageIndex += 1;
		void loadLibrary(false);
	}

	function previousPage(): void {
		if (loading || pageIndex === 0) return;
		pageIndex -= 1;
		void loadLibrary(false);
	}

	async function createNew(): Promise<void> {
		if (pendingAction) return;
		pendingAction = 'create';
		error = null;
		try {
			await createDraft({ template: draftTemplate, timeframe: draftTimeframe });
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

	function askArchive(entry: StrategyLibraryEntry): void {
		archiveError = null;
		archiveTarget = entry;
	}

	async function confirmArchive(): Promise<void> {
		const entry = archiveTarget;
		if (entry === null || !entry.latest_fingerprint || pendingAction) return;
		pendingAction = `archive:${entry.strategy_id}`;
		archiveError = null;
		try {
			await archivePublishedStrategy(entry.latest_fingerprint);
			archiveTarget = null;
			await loadLibrary(false);
		} catch (caught) {
			archiveError = caught instanceof Error ? caught.message : 'Could not archive the strategy.';
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

	function formatDate(value: string): string {
		return formatUtcInputValue(new Date(value)).replace('T', ' ');
	}

	function openRow(event: MouseEvent, entry: StrategyLibraryEntry): void {
		if ((event.target as HTMLElement).closest('a, button, input, select, label')) return;
		void goto(resolve(workspaceHref(entry.strategy_id, 'build')));
	}

	onMount(() => void loadLibrary());
</script>

<svelte:head><title>Strategies · ThyTrader</title></svelte:head>

<main>
	<div class="library-head">
		<div>
			<h1>Strategies</h1>
			<p class="lede">
				Every strategy takes one path: build, test, run on paper, run live, then review why each
				trade happened. Open a strategy to work on it.
			</p>
		</div>
		<section id="library-actions" class="top-actions" aria-label="Library actions">
			<button class="btn" type="button" onclick={openImport}>Import JSON…</button>
			<label class="template-picker"
				>Template
				<select bind:value={draftTemplate} disabled={pendingAction !== null}>
					<option value="ema-trend">EMA trend</option>
					<option value="rsi-mean-reversion">RSI mean reversion</option>
					<option value="macd-trend">MACD trend</option>
					<option value="bollinger-mean-reversion">Bollinger mean reversion</option>
				</select></label
			>
			<label class="template-picker"
				>Clock
				<select bind:value={draftTimeframe} disabled={pendingAction !== null}>
					{#each EXECUTION_TIMEFRAMES as clock (clock)}
						<option value={clock}>{clock}</option>
					{/each}
				</select></label
			>
			<button
				class="btn primary"
				type="button"
				onclick={createNew}
				disabled={pendingAction !== null}
				>{pendingAction === 'create' ? 'Creating…' : 'New strategy'}</button
			>
		</section>
	</div>
	{#if error}<div class="error-banner" role="alert">
			<div>
				<strong>Strategy library unavailable</strong>
				<p>{error}</p>
			</div>
			<button type="button" onclick={() => loadLibrary(false)}>Retry library load</button>
		</div>{/if}
	<section class="card library-card" aria-label="Strategy library">
		{#if loading}
			<div class="loading-region" aria-busy="true"><div class="skeleton wide"></div></div>
		{:else if error && entries.length === 0}
			<div class="library-empty"><p>Could not load strategies. Retry the library load.</p></div>
		{:else if entries.length === 0}
			<div class="library-empty">
				<p>No strategies yet.</p>
				<p class="empty-hint">
					Use <strong>New strategy</strong> above to create a conservative reference draft, or import
					a strategy definition you exported elsewhere.
				</p>
			</div>
		{:else}
			<div class="table-scroll">
				<table aria-label="Strategies">
					<thead>
						<tr>
							<th scope="col">Strategy</th>
							<th scope="col">Market</th>
							<th scope="col">Latest</th>
							<th scope="col">Progress</th>
							<th scope="col" class="num">Latest backtest</th>
							<th scope="col">Updated</th>
							<th scope="col"><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each entries as entry (entry.strategy_id)}
							{@const steps = libraryPipeline(entry)}
							<tr data-strategy-id={entry.strategy_id} onclick={(event) => openRow(event, entry)}>
								<td>
									<a class="strategy-name" href={resolve(workspaceHref(entry.strategy_id, 'build'))}
										>{entry.name}</a
									>
									<span class="fingerprint mono"
										>{entry.latest_fingerprint
											? shortStrategyFingerprint(entry.latest_fingerprint)
											: 'not published'}</span
									>
								</td>
								<td
									>{marketLabel(entry.product_id)}
									<span class="faint">· {entry.timeframe}</span></td
								>
								<td
									><span class="pill" data-status={entry.status}>{libraryVersionLabel(entry)}</span
									></td
								>
								<td>
									<span class="sr-only">{pipelineSummary(steps)}</span>
									<span class="pipe" aria-hidden="true" data-testid="library-pipeline">
										{#each steps as step (step.stage)}
											<span class="step {step.state}" title="{step.stage}: {step.detail}"
												>{step.stage}</span
											>
										{/each}
									</span>
								</td>
								<td class="num">
									{#if entry.backtest}
										<a
											class:pos={compareDecimalStrings(
												entry.backtest.summary.total_return_fraction,
												'0'
											) > 0}
											class:neg={compareDecimalStrings(
												entry.backtest.summary.total_return_fraction,
												'0'
											) < 0}
											href={resolve(
												workspaceHref(entry.strategy_id, 'test', {
													version: entry.latest_fingerprint,
													result: entry.backtest.result_fingerprint
												})
											)}
											aria-label="Latest backtest {formatPercent(
												entry.backtest.summary.total_return_fraction
											)}, {entry.backtest.summary.trade_count} trades"
											>{formatPercent(entry.backtest.summary.total_return_fraction)}</a
										>
										<span class="faint small">{entry.backtest.summary.trade_count} trades</span>
									{:else}
										<span class="faint">—</span>
									{/if}
								</td>
								<td class="faint">{formatDate(entry.updated_at)}</td>
								<td class="row-actions">
									{#if entry.latest_fingerprint}
										<button
											class="btn ghost small"
											type="button"
											disabled={pendingAction !== null}
											onclick={() => void clone(entry)}
											aria-label="Clone {entry.name}"
											>{pendingAction === `clone:${entry.strategy_id}`
												? 'Cloning…'
												: 'Clone'}</button
										>
										<button
											class="btn ghost small"
											type="button"
											disabled={pendingAction !== null}
											onclick={() => askArchive(entry)}
											aria-label="Archive {entry.name}…">Archive…</button
										>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>
	<div class="library-pager" aria-label="Strategy pagination">
		<label
			>Rows per page
			<select data-testid="strategy-page-size" value={pageSize} onchange={changePageSize}>
				{#each [10, 25, 50, 100] as size (size)}<option value={size}>{size}</option>{/each}
			</select>
		</label>
		<span data-testid="strategy-page-range">Page {pageIndex + 1}</span>
		<button
			class="btn"
			type="button"
			onclick={previousPage}
			disabled={loading || pageIndex === 0}
			aria-label="Previous strategy page">Previous</button
		>
		<button
			class="btn"
			type="button"
			onclick={nextPage}
			disabled={loading || nextCursor === null}
			aria-label="Next strategy page">Next</button
		>
	</div>
</main>

<ConfirmDialog
	open={showImport}
	title="Import strategy JSON"
	confirmLabel="Import draft"
	pendingLabel="Importing…"
	pending={pendingAction === 'import'}
	confirmDisabled={importText.trim().length === 0}
	confirmDisabledReason="Paste a strategy definition first."
	error={importHint}
	testId="import-dialog"
	oncancel={closeImport}
	onconfirm={() => void runImport()}
>
	<p>Paste one complete strategy definition. It is stored as a new editable draft.</p>
	<textarea
		bind:value={importText}
		rows={12}
		spellcheck="false"
		aria-label="Strategy definition JSON"></textarea>
</ConfirmDialog>

<ConfirmDialog
	open={archiveTarget !== null}
	title={archiveTarget ? `Archive ${archiveTarget.name}?` : 'Archive strategy?'}
	tone="danger"
	confirmLabel="Archive version"
	pendingLabel="Archiving…"
	pending={pendingAction !== null && pendingAction.startsWith('archive:')}
	error={archiveError}
	testId="archive-dialog"
	oncancel={() => (archiveTarget = null)}
	onconfirm={() => void confirmArchive()}
>
	{#if archiveTarget}
		<div class="row">
			<span>Version</span><span
				>{archiveTarget.latest_version === null
					? 'unknown version'
					: `v${archiveTarget.latest_version}`}</span
			>
		</div>
		<div class="row">
			<span>Fingerprint</span><code class="fp">{archiveTarget.latest_fingerprint}</code>
		</div>
		<p>
			This hides the latest published fingerprint from active selection. Canonical evidence stays
			immutable; older published versions are unchanged.
		</p>
	{/if}
</ConfirmDialog>

<style>
	.library-head {
		display: flex;
		align-items: flex-end;
		flex-wrap: wrap;
		gap: 12px;
		margin-bottom: var(--space-5);
	}
	.library-head h1 {
		margin: 0;
	}
	.top-actions {
		display: flex;
		align-items: flex-end;
		flex-wrap: wrap;
		gap: 8px;
		margin-left: auto;
	}
	.template-picker {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.template-picker select,
	.library-pager select {
		min-height: 34px;
		padding: 0 8px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
	}
	.library-card {
		overflow: hidden;
		margin-bottom: var(--space-4);
	}
	.loading-region {
		padding: 24px;
	}
	.table-scroll {
		overflow-x: auto;
	}
	th,
	td {
		text-align: left;
		vertical-align: middle;
		white-space: nowrap;
	}
	th.num,
	td.num {
		text-align: right;
	}
	tbody tr {
		cursor: pointer;
	}
	tbody tr:hover td {
		background: var(--hover);
	}
	.strategy-name {
		display: block;
		color: var(--text);
		font-weight: 500;
		text-decoration: none;
	}
	.strategy-name:hover {
		text-decoration: underline;
	}
	.fingerprint {
		display: block;
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		display: block;
		font-size: var(--fs-xs);
	}
	.pill {
		display: inline-flex;
		align-items: center;
		height: 24px;
		padding: 0 9px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		font-size: var(--fs-sm);
	}
	.pipe {
		display: flex;
		gap: 4px;
	}
	.step {
		padding: 2px 7px;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--surface-2);
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.step.done {
		color: var(--text);
	}
	.step.active {
		border-style: dashed;
		border-color: var(--line-strong);
		color: var(--text);
	}
	.step.paper {
		border-color: transparent;
		background: var(--accent-soft);
		color: var(--accent);
	}
	.step.live {
		border-color: transparent;
		background: var(--live-soft);
		color: var(--live);
		font-weight: 600;
	}
	td a.pos {
		color: var(--pos);
	}
	td a.neg {
		color: var(--neg);
	}
	.row-actions {
		text-align: right;
	}
	.row-actions .btn + .btn {
		margin-left: 4px;
	}
	.btn.small {
		min-height: 28px;
		padding: 0 8px;
		font-size: var(--fs-sm);
	}
	.library-empty {
		padding: 42px 24px;
		text-align: center;
	}
	.library-empty p {
		margin: 0 0 8px;
	}
	.empty-hint {
		color: var(--muted);
	}
	.library-pager {
		display: flex;
		justify-content: flex-end;
		align-items: center;
		flex-wrap: wrap;
		gap: 12px;
		color: var(--muted);
	}
	.library-pager select {
		margin-left: 8px;
	}
	textarea {
		width: 100%;
		padding: 12px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
		resize: vertical;
	}
	.fp {
		font-size: var(--fs-xs);
		word-break: break-all;
	}
</style>
