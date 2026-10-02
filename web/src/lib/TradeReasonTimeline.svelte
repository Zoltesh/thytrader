<script lang="ts">
	/**
	 * Compact newest-first list of persisted trade reasons
	 * (`GET /api/v1/memory/trade-reasons`). The decision timeline lists here
	 * only the reasons no loaded decision row links by `intent_id` — recorded
	 * before the decision journal existed, discretionary tickets, or bars older
	 * than the rows loaded so far — so a reason is never rendered twice.
	 * Joins only what the payload proves: an order is named only through the
	 * server-composed `reconcile` block.
	 */
	import type { TradeReasonRecord } from '$lib/memory';
	import { formatUtcTimestamp } from '$lib/time';
	import {
		tradeReasonKindLabel,
		tradeReasonNotesText,
		tradeReasonReconcileText,
		tradeReasonTone
	} from '$lib/trade-reasons';

	let {
		records,
		label = 'Trade reasons, newest first',
		deploymentLabel = undefined
	}: {
		records: readonly TradeReasonRecord[];
		/** Accessible name of the list. */
		label?: string;
		/** Names the bot of each reason when the list spans several deployments. */
		deploymentLabel?: (deploymentId: string) => string | null;
	} = $props();
</script>

<ol class="timeline" aria-label={label}>
	{#each records as record (record.id)}
		{@const tone = tradeReasonTone(record)}
		{@const bot = deploymentLabel?.(record.deployment_id) ?? null}
		<li class="tl" data-testid="trade-reason" data-intent-id={record.intent_id}>
			<div class="t mono">
				<time
					datetime={record.signal.candle_starts_at}
					title="Bar start {formatUtcTimestamp(record.signal.candle_starts_at)}"
					>{formatUtcTimestamp(record.signal.candle_starts_at).slice(5, 16)}<span class="utc"
						>&nbsp;UTC</span
					></time
				>
			</div>
			<div>
				<div class="title">
					<span class:pos={tone === 'pos'} class:neg={tone === 'neg'} class:muted={tone === 'muted'}
						>{tradeReasonKindLabel(record)}</span
					>
					· {record.side}
					{record.product_id} · risk {record.risk.decision} ({record.risk.reason_code})
					{#if bot !== null}<span class="bot">{bot}</span>{/if}
				</div>
				<div class="muted">
					Why this intent was persisted: signal {record.signal.last_signal ?? record.signal.kind}
					on the completed bar · {record.origin} · {tradeReasonReconcileText(record)}
				</div>
				<div class="faint">{tradeReasonNotesText(record)}</div>
			</div>
		</li>
	{/each}
</ol>

<style>
	.timeline {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.tl {
		display: flex;
		gap: 14px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.tl:last-child {
		border-bottom: 0;
	}
	.t {
		flex: none;
		width: 108px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.title {
		font-weight: 500;
	}
	.utc {
		font-size: var(--fs-xs);
	}
	.bot {
		margin-left: 6px;
		color: var(--faint);
		font-size: var(--fs-xs);
		font-weight: 400;
	}
	.muted {
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.pos {
		color: var(--pos);
	}
	.neg {
		color: var(--neg);
	}
	@media (max-width: 640px) {
		.tl {
			flex-direction: column;
			gap: 4px;
		}
		.t {
			width: auto;
		}
	}
</style>
