<script lang="ts">
	/**
	 * The strategy library table: one row per strategy with its selection
	 * checkbox, name and fingerprint, tag chips, market, evidence pipeline
	 * (Build / Test / Paper / Live), latest backtest, update time, and Clone /
	 * Delete…. Clicking a row (outside its controls) opens the workspace.
	 */
	import { resolve } from '$app/paths';
	import { compareDecimalStrings, formatPercent } from '$lib/backtests';
	import { marketLabel } from '$lib/deployment-detail';
	import { isResearchTag, type StrategyLibraryEntry } from '$lib/strategies';
	import {
		libraryPipeline,
		pipelineSummary,
		shortStrategyFingerprint,
		workspaceHref
	} from '$lib/strategy-workspace';
	import { formatLibraryDate } from './library';

	let {
		entries,
		selected,
		allSelected,
		someSelected,
		tagFilter,
		pendingAction,
		deleting,
		ontoggleAll,
		ontoggleOne,
		onopen,
		ontag,
		onclone,
		ondelete
	}: {
		entries: StrategyLibraryEntry[];
		/** Selected strategy ids on the current page. */
		selected: string[];
		allSelected: boolean;
		someSelected: boolean;
		tagFilter: string | null;
		pendingAction: string | null;
		deleting: boolean;
		ontoggleAll: () => void;
		ontoggleOne: (strategyId: string) => void;
		onopen: (event: MouseEvent, entry: StrategyLibraryEntry) => void;
		ontag: (tag: string) => void;
		onclone: (entry: StrategyLibraryEntry) => void;
		ondelete: (entry: StrategyLibraryEntry) => void;
	} = $props();
</script>

<div class="table-scroll">
	<table aria-label="Strategies">
		<thead>
			<tr>
				<th scope="col" class="check-col">
					<input
						type="checkbox"
						checked={allSelected}
						indeterminate={someSelected}
						onchange={() => ontoggleAll()}
						aria-label="Select all strategies on this page"
						data-testid="select-all"
					/>
				</th>
				<th scope="col">Strategy</th>
				<th scope="col">Market</th>
				<th scope="col">Progress</th>
				<th scope="col" class="num">Latest backtest</th>
				<th scope="col">Updated</th>
				<th scope="col"><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		<tbody>
			{#each entries as entry (entry.strategy_id)}
				{@const steps = libraryPipeline(entry)}
				<tr
					data-strategy-id={entry.strategy_id}
					class:selected={selected.includes(entry.strategy_id)}
					onclick={(event) => onopen(event, entry)}
				>
					<td class="check-col">
						<input
							type="checkbox"
							checked={selected.includes(entry.strategy_id)}
							onchange={() => ontoggleOne(entry.strategy_id)}
							aria-label="Select {entry.name}"
						/>
					</td>
					<td>
						<a class="strategy-name" href={resolve(workspaceHref(entry.strategy_id, 'build'))}
							>{entry.name}</a
						>
						<span class="fingerprint mono"
							>{entry.current_fingerprint
								? shortStrategyFingerprint(entry.current_fingerprint)
								: 'definition has problems'}</span
						>
						{#if (entry.tags ?? []).length > 0}
							<span class="row-tags">
								{#each entry.tags ?? [] as tag (tag)}
									<button
										class="tag-chip"
										class:active={tag === tagFilter}
										class:research={isResearchTag(tag)}
										type="button"
										data-testid="library-tag-chip"
										title="Show only strategies tagged {tag}"
										onclick={() => ontag(tag)}>{tag}</button
									>
								{/each}
							</span>
						{/if}
					</td>
					<td
						>{entry.product_id ? marketLabel(entry.product_id) : '—'}
						{#if entry.timeframe}<span class="faint">· {entry.timeframe}</span>{/if}</td
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
					<td class="faint">{formatLibraryDate(entry.updated_at)}</td>
					<td class="row-actions">
						<button
							class="btn ghost small"
							type="button"
							disabled={pendingAction !== null}
							onclick={() => onclone(entry)}
							aria-label="Clone {entry.name}"
							>{pendingAction === `clone:${entry.strategy_id}` ? 'Cloning…' : 'Clone'}</button
						>
						<button
							class="btn ghost small"
							type="button"
							disabled={pendingAction !== null || deleting}
							onclick={() => ondelete(entry)}
							aria-label="Delete {entry.name}…">Delete…</button
						>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
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
	.row-tags {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 4px;
	}
	.tag-chip {
		border: 1px solid var(--line-2);
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--muted);
		font-size: var(--fs-xs);
		padding: 1px 8px;
		cursor: pointer;
	}
	.tag-chip:hover,
	.tag-chip.active {
		border-color: var(--accent);
		color: var(--text);
		background: var(--accent-soft);
	}
	.tag-chip.research {
		border-color: var(--info-line);
		color: var(--info);
	}
	.tag-chip.research:hover,
	.tag-chip.research.active {
		border-color: var(--info);
		background: var(--info-soft);
		color: var(--text);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		display: block;
		font-size: var(--fs-xs);
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
	.check-col {
		width: 36px;
	}
	tbody tr.selected td {
		background: var(--accent-soft);
	}
	.neg {
		color: var(--neg);
	}
</style>
