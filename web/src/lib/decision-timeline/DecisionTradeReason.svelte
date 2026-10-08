<script lang="ts">
	/** One persisted trade reason inside an expanded decision row: kind, risk, reconcile, fills, notes. */
	import type { TradeReasonRecord } from '$lib/memory';
	import { formatUtcTimestamp } from '$lib/time';
	import {
		tradeReasonKindLabel,
		tradeReasonNotesText,
		tradeReasonReconcileText
	} from '$lib/trade-reasons';
	import { tradeReasonRiskText } from './view';

	let { reason }: { reason: TradeReasonRecord } = $props();
</script>

<div class="reason" data-testid="decision-trade-reason" data-intent-id={reason.intent_id}>
	<p>
		<b>{tradeReasonKindLabel(reason)}</b> · {reason.side}
		{reason.product_id} · {reason.origin} · recorded {formatUtcTimestamp(reason.created_at)}
	</p>
	<p>{tradeReasonRiskText(reason)}</p>
	<p>{tradeReasonReconcileText(reason)}</p>
	{#if reason.reconcile.fills.length > 0}
		<ul class="plain">
			{#each reason.reconcile.fills as fill (fill.fill_id)}
				<li class="mono small">
					Fill {fill.quantity} @ {fill.price} · fee {fill.fee} · {formatUtcTimestamp(
						fill.filled_at
					)}
				</li>
			{/each}
		</ul>
	{/if}
	<p class="faint">{tradeReasonNotesText(reason)}</p>
</div>

<style>
	.reason {
		display: grid;
		gap: 4px;
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface);
	}
	.reason p {
		margin: 0;
	}
	.plain {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.mono {
		font-family: var(--font-mono);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.faint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
</style>
