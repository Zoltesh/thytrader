<script lang="ts">
	/**
	 * One Home KPI tile (ADR 0084). It renders a `TileView`: a skeleton while its
	 * sources load, a value with supporting lines, or `—` with the reason the
	 * value is unknown (never a guess). Direction and warnings carry a glyph and
	 * words, so color is never the only signal.
	 */
	import type { TileGlyph, TileView } from './home-kpis';

	let {
		label,
		view,
		testId,
		onretry
	}: {
		label: string;
		view: TileView;
		testId: string;
		/** Offered when the tile is unknown because a source failed. */
		onretry?: () => void;
	} = $props();

	const GLYPHS: Record<TileGlyph, { mark: string; word: string }> = {
		up: { mark: '▲', word: 'Up' },
		down: { mark: '▼', word: 'Down' },
		flat: { mark: '■', word: 'Unchanged' },
		warn: { mark: '!', word: 'Attention' }
	};
</script>

<article class="card kpi" data-testid={testId} aria-busy={view.kind === 'loading'}>
	<h3 class="label">{label}</h3>
	{#if view.kind === 'loading'}
		<div class="skeleton value-skeleton" aria-hidden="true"></div>
		<div class="skeleton line-skeleton" aria-hidden="true"></div>
		<p class="sr-only">Loading {label}…</p>
	{:else}
		<p class="value" class:unknown={view.kind === 'unknown'} data-testid="{testId}-value">
			{#if view.kind === 'value'}
				{view.value}{#if view.unit}<span class="unit">{view.unit}</span>{/if}
			{:else}
				<span aria-hidden="true">—</span><span class="sr-only">Unknown</span>
			{/if}
		</p>
		{#if view.kind === 'unknown'}
			<p class="line muted reason">{view.reason}</p>
		{/if}
		{#each view.lines as line, index (index)}
			<p class="line {line.tone}">
				{#if line.glyph}<span class="glyph glyph-{line.glyph}" aria-hidden="true"
						>{GLYPHS[line.glyph].mark}</span
					><span class="sr-only">{GLYPHS[line.glyph].word}: </span>{/if}{line.text}
			</p>
		{/each}
		{#if view.kind === 'unknown' && view.retryable && onretry}
			<button type="button" class="btn ghost retry" onclick={onretry}>Retry</button>
		{/if}
	{/if}
</article>

<style>
	.kpi {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
		padding: 14px 16px;
	}
	.label {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
		font-weight: 500;
	}
	.value {
		margin: 4px 0 2px;
		color: var(--text);
		font-size: var(--fs-2xl);
		font-weight: 600;
		letter-spacing: -0.02em;
		line-height: 1.2;
		overflow-wrap: anywhere;
	}
	.value.unknown {
		color: var(--faint);
	}
	.unit {
		margin-left: 6px;
		color: var(--muted);
		font-size: var(--fs-base);
		font-weight: 500;
		letter-spacing: 0;
	}
	.line {
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
		line-height: 1.4;
	}
	.line.muted {
		color: var(--faint);
	}
	.line.pos {
		color: var(--pos);
	}
	.line.neg {
		color: var(--neg);
	}
	.line.warn {
		color: var(--warn);
	}
	.reason {
		color: var(--muted);
	}
	.glyph {
		display: inline-block;
		min-width: 1.1em;
		margin-right: 4px;
		font-size: 10px;
		font-weight: 700;
		text-align: center;
	}
	.glyph-warn {
		border: 1px solid currentColor;
		border-radius: 50%;
		line-height: 1.3;
	}
	.retry {
		align-self: flex-start;
		min-height: 28px;
		margin-top: 6px;
		padding: 0 8px;
	}
	.value-skeleton {
		width: 60%;
		height: 28px;
		margin: 6px 0 6px;
	}
	.line-skeleton {
		width: 80%;
		height: 12px;
		margin: 0;
	}
</style>
