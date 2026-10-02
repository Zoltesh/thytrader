<script lang="ts" module>
	/** Load state of one attention source, shown under the list until it is fully read. */
	export type AttentionSourceStatus = {
		id: 'bots' | 'setup' | 'datasets' | 'research';
		/** Plain noun phrase, e.g. "watched datasets". */
		label: string;
		state: 'loading' | 'ready' | 'partial' | 'error';
		/** Why a source is partial or failed; a loading hint otherwise. */
		note: string | null;
		retry?: () => void;
	};
</script>

<script lang="ts">
	/**
	 * Home "Needs attention" (ADR 0084). Items come pre-sorted from
	 * `home-attention.ts`. Each shows an icon and a headline in words (never
	 * color alone), a severity read to screen readers, a LIVE tag for real
	 * money, and one action that links to where the problem is fixed. Sources
	 * still loading, partial, or failed are listed under the items, so an empty
	 * list never claims "all clear" for a source that was not read.
	 */
	import { resolve } from '$app/paths';
	import type { AttentionIcon, AttentionItem } from './home-attention';

	let {
		items,
		sources,
		onDataHealth
	}: {
		items: readonly AttentionItem[];
		sources: readonly AttentionSourceStatus[];
		/** Open Home's Data health disclosure (dataset items without a strategy). */
		onDataHealth: () => void;
	} = $props();

	/** Items shown before "Show N more", keeping the card level with the chart. */
	const COLLAPSED_COUNT = 5;
	let expanded = $state(false);

	const visible = $derived(expanded ? items : items.slice(0, COLLAPSED_COUNT));
	const hiddenCount = $derived(Math.max(0, items.length - COLLAPSED_COUNT));
	const pending = $derived(sources.filter((source) => source.state !== 'ready'));
	const settled = $derived(sources.every((source) => source.state !== 'loading'));
	const anyFailed = $derived(sources.some((source) => source.state === 'error'));

	const ICONS: Record<AttentionIcon, string[]> = {
		pause: ['M7 5h3v14H7z', 'M14 5h3v14h-3z'],
		mismatch: ['M12 7v6', 'M12 16.5h.01', 'M12 3l9 16H3z'],
		status: ['M12 8v5', 'M12 16h.01', 'M12 3a9 9 0 110 18 9 9 0 010-18z'],
		breaker: ['M13 3L5 14h6l-1 7 8-11h-6z'],
		shield: ['M12 3l7 3v6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z', 'M12 9v4', 'M12 16h.01'],
		key: ['M8 11a4 4 0 110 8 4 4 0 010-8z', 'M11 12l8-8', 'M16 7l3 3'],
		policy: ['M7 3h7l5 5v13H7z', 'M14 3v5h5', 'M10 13h6', 'M10 17h6'],
		data: [
			'M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3z',
			'M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6',
			'M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3'
		],
		clock: ['M12 4a8 8 0 110 16 8 8 0 010-16z', 'M12 8v4l3 2'],
		gap: ['M3 12h6', 'M15 12h6', 'M10 7l4 10'],
		research: ['M9 3h6', 'M10 3v6l-5 10a1.5 1.5 0 001.3 2h11.4a1.5 1.5 0 001.3-2L14 9V3']
	};

	function sourceText(source: AttentionSourceStatus): string {
		if (source.state === 'loading') return source.note ?? `Checking ${source.label}…`;
		if (source.state === 'error')
			return `Couldn't check ${source.label}: ${source.note ?? 'the request failed.'}`;
		return `${source.label.charAt(0).toUpperCase()}${source.label.slice(1)}: ${source.note ?? 'partly checked.'}`;
	}

	function showDataHealth(event: MouseEvent): void {
		event.preventDefault();
		onDataHealth();
	}
</script>

<section class="card attention" aria-labelledby="attention-title" data-testid="needs-attention">
	<div class="card-head">
		<h2 id="attention-title">Needs attention</h2>
		<span
			class="chip count"
			class:has-items={items.length > 0}
			data-testid="attention-count"
			aria-label="{items.length} {items.length === 1 ? 'item' : 'items'}">{items.length}</span
		>
	</div>
	{#if items.length > 0}
		<ul class="items" role="list">
			{#each visible as item (item.id)}
				<li
					class="att"
					data-testid="attention-item"
					data-category={item.category}
					data-severity={item.severity}
				>
					<span class="icon {item.severity}" aria-hidden="true">
						<svg
							width="14"
							height="14"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2.2"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							{#each ICONS[item.icon] as path (path)}<path d={path}></path>{/each}
						</svg>
					</span>
					<div class="text">
						<p class="title">
							<span class="sr-only"
								>{item.severity === 'critical' ? 'Critical' : 'Needs attention'}:
							</span>{#if item.live}<span class="chip live tag">LIVE</span>{/if}{item.label}
						</p>
						<p class="detail">{item.detail}</p>
					</div>
					{#if 'href' in item.action}
						<a
							class="btn action"
							href={resolve(item.action.href)}
							aria-label="{item.action.label}: {item.label}">{item.action.label}</a
						>
					{:else}
						<a
							class="btn action"
							href="#data-health"
							aria-label="{item.action.label}: {item.label}"
							onclick={showDataHealth}>{item.action.label}</a
						>
					{/if}
				</li>
			{/each}
		</ul>
		{#if hiddenCount > 0}
			<button
				type="button"
				class="btn ghost more"
				aria-expanded={expanded}
				onclick={() => (expanded = !expanded)}
			>
				{expanded ? 'Show fewer' : `Show ${hiddenCount} more`}
			</button>
		{/if}
	{:else if settled && !anyFailed}
		<div class="empty" data-testid="attention-empty">
			<span class="icon ok" aria-hidden="true">
				<svg
					width="14"
					height="14"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2.4"
					stroke-linecap="round"
					stroke-linejoin="round"><path d="M5 12l4 4 10-10"></path></svg
				>
			</span>
			<div>
				<p class="title">Nothing needs you right now</p>
				<p class="detail">
					Checked bots, credentials and risk policy, watched datasets, and recent research jobs.
				</p>
			</div>
		</div>
	{:else if settled}
		<p class="quiet" data-testid="attention-empty">
			Nothing found in the sources that loaded. The rest could not be checked.
		</p>
	{:else}
		<div class="checking" aria-hidden="true">
			<div class="skeleton row-skeleton"></div>
			<div class="skeleton row-skeleton"></div>
		</div>
	{/if}
	{#if pending.length > 0}
		<ul class="sources" role="list" aria-live="polite" data-testid="attention-sources">
			{#each pending as source (source.id)}
				<li class="source {source.state}" data-source={source.id}>
					<span>{sourceText(source)}</span>
					{#if source.state === 'error' && source.retry}
						<button type="button" class="btn ghost retry" onclick={source.retry}>Retry</button>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.attention {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.count {
		margin-left: auto;
	}
	.count.has-items {
		border-color: var(--warn-line);
		color: var(--warn);
	}
	.items,
	.sources {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.att,
	.empty {
		display: flex;
		align-items: flex-start;
		gap: 12px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.items li:last-child {
		border-bottom: 0;
	}
	.icon {
		display: grid;
		flex: none;
		place-items: center;
		width: 24px;
		height: 24px;
		border-radius: var(--radius-sm);
	}
	.icon.critical {
		background: var(--danger-soft);
		color: var(--neg);
	}
	.icon.warning {
		background: var(--warn-soft);
		color: var(--warn);
	}
	.icon.ok {
		background: var(--accent-soft);
		color: var(--accent);
	}
	.text {
		flex: 1;
		min-width: 0;
	}
	.title {
		margin: 0;
		color: var(--text);
		font-weight: 500;
		overflow-wrap: anywhere;
	}
	.tag {
		height: 18px;
		margin-right: 6px;
		padding: 0 6px;
		font-size: 10.5px;
		vertical-align: 1px;
	}
	.detail {
		margin: 2px 0 0;
		color: var(--muted);
		font-size: var(--fs-sm);
		overflow-wrap: anywhere;
	}
	.action {
		flex: none;
		min-height: 30px;
		font-size: var(--fs-sm);
	}
	.more {
		align-self: flex-start;
		margin: 6px 10px;
		min-height: 30px;
		font-size: var(--fs-sm);
	}
	.empty {
		border-bottom: 0;
	}
	.quiet {
		margin: 0;
		padding: 14px 16px;
		color: var(--muted);
	}
	.checking {
		padding: 4px 16px;
	}
	.row-skeleton {
		height: 36px;
		margin: 10px 0;
	}
	.sources {
		margin-top: auto;
		padding: 8px 16px 10px;
		border-top: 1px solid var(--line);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.source {
		display: flex;
		align-items: center;
		gap: 8px;
		min-height: 24px;
	}
	.source.error {
		color: var(--warn);
	}
	.source.partial {
		color: var(--muted);
	}
	.retry {
		min-height: 24px;
		padding: 0 8px;
		font-size: var(--fs-sm);
	}
</style>
