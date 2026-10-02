<script lang="ts">
	/**
	 * Home "Holdings" (ADR 0084): the Coinbase (or demo) balances as a compact
	 * table. Sorting keeps the three-state header cycle and sorts before
	 * trimming, so the compact view always shows the globally largest rows; dust
	 * collapses into an expandable summary; unvalued assets are disclosed. A
	 * failed refresh keeps the last snapshot on screen with the redacted error.
	 */
	import {
		nextAssetSort,
		paginateAssets,
		sortAssets,
		type AssetSort,
		type AssetSortKey
	} from '$lib/asset-table';
	import { analyzeDust, formatQuantityDisplay } from '$lib/money';
	import { formatUsd, type Portfolio, type PortfolioAsset } from '$lib/portfolio';
	import { formatAge } from './home-format';
	import { STALE_SNAPSHOT_MS } from './home-kpis';
	import type { Load } from './load';

	let {
		portfolio,
		nowMs,
		onrefresh
	}: {
		portfolio: Load<Portfolio>;
		nowMs: number;
		onrefresh: () => void;
	} = $props();

	const COLUMNS: Array<{ key: AssetSortKey; label: string }> = [
		{ key: 'currency', label: 'Asset' },
		{ key: 'available', label: 'Available' },
		{ key: 'hold', label: 'On hold' },
		{ key: 'total', label: 'Total' },
		{ key: 'value', label: 'Est. value' }
	];
	/** Rows in the compact view; "Show all" lists every non-dust balance. */
	const COMPACT_ROWS = 10;

	let sort = $state<AssetSort | null>({ key: 'value', direction: 'desc' });
	let showAll = $state(false);
	let dustOpen = $state(false);

	const current = $derived(portfolio.data);
	const dust = $derived(current === null ? null : analyzeDust(current.assets));
	const sorted = $derived(dust === null ? [] : sortAssets(dust.visible, sort));
	const rows = $derived(showAll ? sorted : paginateAssets(sorted, 1, COMPACT_ROWS).items);
	const ageMs = $derived(current === null ? null : nowMs - Date.parse(current.as_of));
	const stale = $derived(ageMs !== null && Number.isFinite(ageMs) && ageMs > STALE_SNAPSHOT_MS);
	const refreshing = $derived(portfolio.status === 'loading' && current !== null);

	function sortOn(key: AssetSortKey): void {
		sort = nextAssetSort(sort, key);
	}

	function ariaSort(key: AssetSortKey): 'ascending' | 'descending' | 'none' {
		if (sort?.key !== key) return 'none';
		return sort.direction === 'asc' ? 'ascending' : 'descending';
	}

	function valueText(asset: PortfolioAsset): string {
		return asset.value ? formatUsd(asset.value.amount) : 'Unavailable';
	}
</script>

<section class="card holdings" aria-labelledby="holdings-title" data-testid="holdings">
	<div class="card-head">
		<div class="titles">
			<h2 id="holdings-title">Holdings</h2>
			<p class="sub">
				{#if current !== null}{current.assets.length}
					{current.assets.length === 1 ? 'balance' : 'balances'} ·
				{/if}USD estimate · Coinbase spot balances
			</p>
		</div>
		{#if stale && ageMs !== null}
			<span class="stale" role="status">
				<span aria-hidden="true">!</span> Snapshot {formatAge(ageMs)} old · refresh for current balances
			</span>
		{/if}
		<button
			type="button"
			class="btn ghost refresh-button"
			onclick={onrefresh}
			disabled={portfolio.status === 'loading'}
		>
			<span class:spinning={portfolio.status === 'loading'} aria-hidden="true">↻</span>
			{refreshing ? 'Refreshing…' : 'Refresh balances'}
		</button>
	</div>

	{#if portfolio.status === 'error'}
		<div class="error-banner inline-error" role="alert">
			<div>
				<strong>Couldn't refresh Coinbase</strong>
				<p>{portfolio.error}</p>
			</div>
			<button type="button" onclick={onrefresh}>Try again</button>
		</div>
	{/if}

	{#if current === null}
		{#if portfolio.status === 'loading'}
			<div class="body" aria-label="Loading holdings">
				<div class="skeleton row-skeleton"></div>
				<div class="skeleton row-skeleton"></div>
				<div class="skeleton row-skeleton"></div>
			</div>
		{/if}
	{:else if current.demo && current.assets.length === 0}
		<p class="table-empty">No balances yet. Add Coinbase credentials in Settings to see yours.</p>
	{:else}
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						{#each COLUMNS as column (column.key)}
							<th scope="col" aria-sort={ariaSort(column.key)}>
								<button
									type="button"
									class="sort-header"
									class:active={sort?.key === column.key}
									onclick={() => sortOn(column.key)}
								>
									{column.label}
									<span class="sort-arrow" aria-hidden="true">
										{sort?.key === column.key ? (sort.direction === 'asc' ? '▲' : '▼') : '↕'}
									</span>
								</button>
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each rows as asset (asset.currency)}
						{@const available = formatQuantityDisplay(asset.available)}
						{@const hold = formatQuantityDisplay(asset.hold)}
						{@const total = formatQuantityDisplay(asset.total)}
						<tr data-testid="holding-row">
							<td>
								<div class="asset-name">
									<span class="coin" aria-hidden="true">{asset.currency.slice(0, 1)}</span>
									<div><strong>{asset.name}</strong><small>{asset.currency}</small></div>
								</div>
							</td>
							<td class="num" title={available.title}>{available.text}</td>
							<td class="num" title={hold.title}>{hold.text}</td>
							<td class="num" title={total.title}>{total.text}</td>
							<td class="num asset-value">{valueText(asset)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if sorted.length === 0}
			<p class="table-empty">No balances with value to display.</p>
		{/if}
		{#if sorted.length > COMPACT_ROWS}
			<div class="foot">
				<span data-testid="holdings-range">Showing {rows.length} of {sorted.length}</span>
				<button
					type="button"
					class="btn ghost"
					aria-expanded={showAll}
					onclick={() => (showAll = !showAll)}
				>
					{showAll ? `Show top ${COMPACT_ROWS}` : `Show all ${sorted.length}`}
				</button>
			</div>
		{/if}
		{#if current.unvalued_assets.length > 0}
			<p class="unvalued">No direct USD valuation: {current.unvalued_assets.join(', ')}</p>
		{/if}
		{#if dust !== null && dust.dust.length > 0 && dust.dustTotal !== null}
			<div class="dust-summary">
				<button
					type="button"
					class="dust-toggle"
					aria-expanded={dustOpen}
					onclick={() => (dustOpen = !dustOpen)}
				>
					<span aria-hidden="true">{dustOpen ? '▾' : '▸'}</span>
					{dust.dust.length}
					{dust.dust.length === 1 ? 'balance' : 'balances'} under
					{formatUsd('0.10')} totaling {formatUsd(dust.dustTotal)}
				</button>
				{#if dustOpen}
					<ul class="dust-list">
						{#each dust.dust as asset (asset.currency)}
							<li>{asset.name} ({asset.currency}) · {valueText(asset)}</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
	{/if}
</section>

<style>
	.holdings {
		overflow: hidden;
	}
	.card-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px 14px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.titles {
		margin-right: auto;
	}
	.sub {
		margin: 2px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.stale {
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.refresh-button {
		min-height: 30px;
		font-size: var(--fs-sm);
	}
	.inline-error {
		margin: 12px 16px 0;
	}
	.body {
		padding: 4px 16px 12px;
	}
	.row-skeleton {
		height: 30px;
		margin: 10px 0;
	}
	th {
		padding: 8px 16px;
		border-bottom: 1px solid var(--line);
	}
	td {
		padding: 8px 16px;
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.coin {
		width: 26px;
		height: 26px;
		font-size: var(--fs-sm);
	}
	.foot {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		padding: 8px 16px;
		border-top: 1px solid var(--line);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.unvalued {
		margin: 0;
		padding: 10px 16px;
	}
	.dust-summary {
		padding: 6px 16px 12px;
	}
	.dust-toggle {
		padding: 4px 0;
		border: none;
		background: transparent;
		color: var(--faint);
		font-size: var(--fs-sm);
		cursor: pointer;
	}
	.dust-toggle:hover {
		color: var(--muted);
	}
	.dust-list {
		margin: 6px 0 0;
		padding-left: 18px;
		color: var(--faint);
		font: 400 var(--fs-sm) var(--font-mono);
	}
	.table-empty {
		margin: 0;
	}
</style>
