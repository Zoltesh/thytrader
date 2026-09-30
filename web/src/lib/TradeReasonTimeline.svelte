<script lang="ts" module>
	import type { TradeReasonRecord } from '$lib/memory';

	export type TradeReasonState =
		| { status: 'loading' }
		| { status: 'error'; message: string }
		| { status: 'ready'; records: TradeReasonRecord[] };
</script>

<script lang="ts">
	/**
	 * "Why it traded" timeline for one deployment, newest first: its latest
	 * completed-bar signal, then every persisted trade reason
	 * (`GET /api/v1/memory/trade-reasons?deployment_id=`). Shared by the Why
	 * stage and bot detail. Full per-bar history is not recorded, so a missing
	 * reason never becomes "conditions did not match".
	 */
	import type { Deployment } from '$lib/deployments';
	import { latestSignalExplanation } from '$lib/strategy-workspace';
	import { formatUtcTimestamp } from '$lib/time';
	import {
		tradeReasonKindLabel,
		tradeReasonNotesText,
		tradeReasonReconcileText,
		tradeReasonTone
	} from '$lib/trade-reasons';

	let { deployment, reasons }: { deployment: Deployment; reasons: TradeReasonState | undefined } =
		$props();

	const signal = $derived(latestSignalExplanation(deployment));
</script>

{#snippet when(value: string | null)}
	{#if value}
		<time datetime={value} title={formatUtcTimestamp(value)}
			>{formatUtcTimestamp(value).slice(5, 16)}<span class="utc">&nbsp;UTC</span></time
		>
	{:else}
		—
	{/if}
{/snippet}

<ol class="timeline" aria-label="Decisions for this deployment, newest first">
	<li class="tl latest" data-testid="latest-signal" data-kind={signal.kind}>
		<div class="t mono">{@render when(deployment.last_evaluated_bar)}</div>
		<div>
			<div class="title"><span class="tag">Latest bar</span> {signal.title}</div>
			<div class="muted">{signal.detail}</div>
		</div>
	</li>
	{#if reasons === undefined || reasons.status === 'loading'}
		<li class="tl">
			<div class="t"></div>
			<div class="muted">Loading trade reasons…</div>
		</li>
	{:else if reasons.status === 'error'}
		<li class="tl" role="alert">
			<div class="t"></div>
			<div class="problem">Trade reasons could not be loaded: {reasons.message}</div>
		</li>
	{:else if reasons.records.length === 0}
		<li class="tl" data-testid="no-trade-reasons">
			<div class="t"></div>
			<div>
				<div class="title">No recorded trade rationale for this deployment</div>
				<div class="muted">
					This can mean no intent was persisted, or rationale recording was unavailable. Orders
					cannot be matched to intents from the deployment response.
				</div>
			</div>
		</li>
	{:else}
		{#each reasons.records as record (record.id)}
			{@const tone = tradeReasonTone(record)}
			<li class="tl" data-testid="trade-reason">
				<div class="t mono">{@render when(record.signal.candle_starts_at)}</div>
				<div>
					<div class="title">
						<span
							class:pos={tone === 'pos'}
							class:neg={tone === 'neg'}
							class:muted={tone === 'muted'}>{tradeReasonKindLabel(record)}</span
						>
						· {record.side}
						{record.product_id} · risk {record.risk.decision} ({record.risk.reason_code})
					</div>
					<div class="muted">
						Why this intent was persisted: signal {record.signal.last_signal ?? record.signal.kind}
						on the completed bar · {record.origin} · {tradeReasonReconcileText(record)}
					</div>
					<div class="faint">{tradeReasonNotesText(record)}</div>
				</div>
			</li>
		{/each}
	{/if}
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
	.tag {
		margin-right: 4px;
		color: var(--faint);
		font-size: var(--fs-xs);
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.05em;
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
	.problem {
		color: var(--neg);
	}
	.latest {
		background: var(--surface-2);
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
