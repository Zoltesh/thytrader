<script lang="ts">
	/**
	 * One journaled bar in the decision timeline: a disclosure button (bar close
	 * time, outcome chip, product, bot, server-written reason) that expands into
	 * `DecisionDetail`.
	 */
	import {
		decisionOutcomeLabel,
		decisionOutcomeTone,
		type BarDecision,
		type TradeReasonAssignment
	} from '$lib/decisions';
	import { formatUtcTimestamp } from '$lib/time';
	import type { TradeReasonState } from '$lib/trade-reasons';
	import DecisionDetail from './DecisionDetail.svelte';

	let {
		decision,
		key,
		idBase,
		open,
		showProduct,
		bot,
		assignment,
		rowByKey,
		reasons,
		ontoggle
	}: {
		decision: BarDecision;
		/** This row's `decisionKey`. */
		key: string;
		/** Prefix for this row's element ids. */
		idBase: string;
		open: boolean;
		/** Name the row's product (multi-product bots or mixed rows). */
		showProduct: boolean;
		/** The row's bot when the timeline spans several deployments, else null. */
		bot: string | null;
		assignment: TradeReasonAssignment;
		rowByKey: Map<string, BarDecision>;
		reasons: TradeReasonState | undefined;
		ontoggle: () => void;
	} = $props();

	const tone = $derived(decisionOutcomeTone(decision.outcome));
</script>

{#snippet barTime(value: string)}
	<time datetime={value} title="Bar closed {formatUtcTimestamp(value)}"
		><span class="sr-only">Bar closed</span>
		{formatUtcTimestamp(value).slice(5, 16)}<span class="utc">&nbsp;UTC</span></time
	>
{/snippet}

<li
	class="row"
	class:open
	data-testid="decision-row"
	data-outcome={decision.outcome}
	data-product={decision.product_id}
	data-deployment-id={decision.deployment_id}
>
	<button
		type="button"
		class="row-head"
		aria-expanded={open}
		aria-controls={open ? `${idBase}-detail` : undefined}
		onclick={() => ontoggle()}
	>
		<span class="t">{@render barTime(decision.bar_closes_at)}</span>
		<span class="chip outcome tone-{tone}" data-testid="decision-outcome"
			>{decisionOutcomeLabel(decision.outcome)}</span
		>
		{#if showProduct}
			<span class="product" data-testid="decision-product">{decision.product_id}</span>
		{/if}
		{#if bot !== null}
			<span class="bot" data-testid="decision-bot">{bot}</span>
		{/if}
		<span class="summary" data-testid="decision-summary">{decision.summary}</span>
		<span class="caret" aria-hidden="true">›</span>
	</button>
	{#if open}
		<div class="detail" id="{idBase}-detail" data-testid="decision-detail">
			<DecisionDetail {decision} {key} {idBase} {assignment} {rowByKey} {reasons} />
		</div>
	{/if}
</li>

<style>
	.row {
		border-bottom: 1px solid var(--line);
	}
	.row-head {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 10px 16px;
		border: 0;
		background: transparent;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}
	.row-head:hover {
		background: var(--hover);
	}
	.row.open .row-head {
		background: var(--surface-2);
	}
	.t {
		flex: none;
		width: 118px;
		color: var(--faint);
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
		white-space: nowrap;
	}
	.utc {
		font-size: var(--fs-xs);
	}
	.outcome {
		flex: none;
	}
	.chip.tone-pos {
		border-color: var(--accent-line);
		background: var(--accent-soft);
		color: var(--pos);
	}
	.chip.tone-neg {
		border-color: var(--danger-line);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.chip.tone-warn {
		border-color: var(--warn-line);
		background: var(--warn-soft);
		color: var(--warn);
	}
	.chip.tone-info {
		border-color: var(--info-line);
		background: var(--info-soft);
		color: var(--info);
	}
	.chip.tone-muted {
		color: var(--muted);
	}
	.product {
		flex: none;
		color: var(--muted);
		font-family: var(--font-mono);
		font-size: var(--fs-xs);
	}
	.bot {
		flex: none;
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.summary {
		flex: 1;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.caret {
		flex: none;
		color: var(--faint);
		font-size: var(--fs-lg);
		line-height: 1;
	}
	.row.open .caret {
		transform: rotate(90deg);
	}
	.detail {
		display: grid;
		gap: 14px;
		/* Aligns with the outcome chip: row padding 16 + time column 118 + gap 10. */
		padding: 6px 16px 16px 144px;
		background: var(--surface-2);
	}
	@media (prefers-reduced-motion: no-preference) {
		.caret {
			transition: transform 0.12s;
		}
	}
	@media (max-width: 640px) {
		.row-head {
			flex-wrap: wrap;
			gap: 6px 10px;
		}
		.t {
			width: auto;
		}
		.summary {
			flex-basis: 100%;
			order: 5;
		}
		.detail {
			padding-left: 16px;
		}
	}
</style>
