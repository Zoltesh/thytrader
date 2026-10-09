<script lang="ts">
	/**
	 * Manager proposals (ADR 0091): the pending proposals with Approve / Decline /
	 * Ask why, the recently decided ones, and the approval dialog (with the live
	 * acknowledgement when approving resumes a live sleeve).
	 *
	 * The Manager tab owns loading the proposals; this component owns the decision
	 * and hands the recorded outcome back through `ondecision`.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { askAgent } from '$lib/agent-request.svelte';
	import {
		approvalNeedsLiveAck,
		askWhyPrompt,
		decideProposal,
		errorText,
		proposalKindLabel,
		proposalStatusText,
		utcMinute,
		type Portfolio,
		type Proposal
	} from '$lib/portfolios';

	let {
		portfolio,
		proposals,
		proposalsError,
		ondecision,
		onreload
	}: {
		portfolio: Portfolio;
		proposals: Proposal[];
		proposalsError: string | null;
		/** After a decision was recorded: show the notice and reload. */
		ondecision: (notice: string) => Promise<void>;
		/** After a decision failed: reload the proposals. */
		onreload: () => Promise<void>;
	} = $props();

	let approving = $state<Proposal | null>(null);
	let approveNote = $state('');
	let liveAcknowledged = $state(false);
	let deciding = $state(false);
	let decisionError = $state<string | null>(null);

	const pending = $derived(proposals.filter((item) => item.status === 'pending'));
	const decided = $derived(proposals.filter((item) => item.status !== 'pending').slice(0, 5));
	const approvalNeedsAck = $derived(
		approving !== null && approvalNeedsLiveAck(portfolio.mode, approving)
	);

	function openApprove(proposal: Proposal): void {
		approving = proposal;
		approveNote = '';
		liveAcknowledged = false;
		decisionError = null;
	}

	async function decide(proposal: Proposal, decision: 'approve' | 'decline'): Promise<void> {
		deciding = true;
		decisionError = null;
		try {
			await decideProposal(portfolio.portfolio_id, proposal.proposal_id, decision, {
				note: decision === 'approve' ? approveNote.trim() : undefined,
				liveAcknowledged: decision === 'approve' && approvalNeedsAck && liveAcknowledged
			});
			approving = null;
			await ondecision(
				decision === 'approve' ? `Approved: ${proposal.summary}` : `Declined: ${proposal.summary}`
			);
		} catch (caught) {
			decisionError = errorText(caught, 'The decision could not be recorded.');
			await onreload();
		} finally {
			deciding = false;
		}
	}

	function askWhy(proposal: Proposal): void {
		askAgent(`Proposal: ${proposal.summary}`, askWhyPrompt(portfolio, proposal));
	}
</script>

<section class="card proposals" aria-label="Manager proposals" data-testid="manager-proposals">
	<div class="journal-head">
		<h2>Proposals</h2>
		<span class="muted small"
			>{pending.length === 0 ? 'Nothing waiting' : `${pending.length} waiting for you`}</span
		>
	</div>
	{#if proposalsError}
		<p class="problem small body-pad" role="alert">{proposalsError}</p>
	{:else if pending.length === 0}
		<p class="muted body-pad" data-testid="no-proposals">
			No proposal is waiting. The manager agent submits them with its reasons and the evidence it
			cites; those inside its permissions apply on their own and show in the journal.
		</p>
	{/if}
	{#each pending as proposal (proposal.proposal_id)}
		<article class="proposal" data-testid="proposal-card">
			<div class="when mono">{utcMinute(proposal.created_at)}</div>
			<div class="what">
				<div class="kind">
					<span class="chip small-chip">{proposalKindLabel(proposal.kind)}</span>
					{proposal.summary}
				</div>
				<p class="rationale">{proposal.rationale}</p>
				{#if proposal.evidence.length > 0}
					<ul class="evidence">
						{#each proposal.evidence as item, index (index)}
							<li class="mono small">{item.kind} · {item.ref}</li>
						{/each}
					</ul>
				{/if}
				{#if proposal.approval_reason}
					<p class="faint small">Needs your approval: {proposal.approval_reason}</p>
				{/if}
				<div class="proposal-actions">
					<button
						type="button"
						class="btn"
						disabled={deciding}
						onclick={() => void decide(proposal, 'decline')}>Decline</button
					>
					<button type="button" class="btn" onclick={() => askWhy(proposal)}>Ask why</button>
					<button
						type="button"
						class="btn primary"
						disabled={deciding}
						onclick={() => openApprove(proposal)}>Approve…</button
					>
				</div>
			</div>
		</article>
	{/each}
	{#if decided.length > 0}
		<h3 class="body-pad decided-title">Recently decided</h3>
		<ol class="journal" data-testid="decided-proposals">
			{#each decided as proposal (proposal.proposal_id)}
				<li class="entry">
					<div class="when mono">{utcMinute(proposal.decided_at ?? proposal.created_at)}</div>
					<div class="what">
						<div class="kind">{proposalStatusText(proposal)}</div>
						<div>{proposal.summary}</div>
					</div>
				</li>
			{/each}
		</ol>
	{/if}
	{#if decisionError && approving === null}<p class="problem small body-pad" role="alert">
			{decisionError}
		</p>{/if}
</section>

<ConfirmDialog
	open={approving !== null}
	title="Approve this proposal?"
	tone={approvalNeedsAck ? 'live' : 'default'}
	liveChip={portfolio.mode === 'live'}
	confirmLabel={approvalNeedsAck ? 'Approve and resume live' : 'Approve'}
	pendingLabel="Approving…"
	pending={deciding}
	confirmDisabled={approvalNeedsAck && !liveAcknowledged}
	confirmDisabledReason="Tick the acknowledgement to continue."
	error={decisionError}
	testId="approve-dialog"
	oncancel={() => (approving = null)}
	onconfirm={() => {
		if (approving !== null) void decide(approving, 'approve');
	}}
>
	{#if approving !== null}
		<p><b>{approving.summary}</b></p>
		<p class="muted">{approving.rationale}</p>
		<p class="faint small">
			The change is checked against the portfolio as it is now; if it no longer fits, the proposal
			is recorded as not applied and nothing changes.
		</p>
		<label class="field">
			<span>Note (optional, journaled)</span>
			<input type="text" maxlength="500" bind:value={approveNote} />
		</label>
		{#if approvalNeedsAck}
			<label class="live-ack">
				<input type="checkbox" bind:checked={liveAcknowledged} data-testid="approve-live-ack" />
				<span
					>I understand resuming this sleeve places real orders on Coinbase with real money.</span
				>
			</label>
		{/if}
	{/if}
</ConfirmDialog>

<style>
	.proposals {
		margin-bottom: 16px;
	}
	.proposal {
		display: flex;
		gap: 14px;
		padding: 14px 18px;
		border-bottom: 1px solid var(--line);
		background: var(--accent-soft, var(--surface-2));
	}
	.rationale {
		margin: 6px 0 0;
		color: var(--muted);
		white-space: pre-line;
	}
	.evidence {
		margin: 6px 0 0;
		padding: 0;
		list-style: none;
		color: var(--faint);
		overflow-wrap: anywhere;
	}
	.proposal-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin-top: 10px;
	}
	.decided-title {
		margin: 0;
	}
	.small-chip {
		height: 18px;
		margin-right: 6px;
		font-size: var(--fs-xs);
	}
	.live-ack {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 12px;
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
		cursor: pointer;
	}
	.live-ack input {
		margin-top: 2px;
	}
	.body-pad {
		padding: 12px 18px;
	}
	.journal-head {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 14px 18px;
		border-bottom: 1px solid var(--line);
	}
	.journal-head .muted {
		margin-left: auto;
	}
	h3 {
		margin: 16px 0 6px;
		color: var(--faint);
		font-size: var(--fs-sm);
		font-weight: 500;
	}
	.field {
		display: grid;
		gap: 6px;
		margin-top: 12px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.field input {
		padding: 6px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	.journal {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.entry {
		display: flex;
		gap: 14px;
		padding: 12px 18px;
		border-bottom: 1px solid var(--line);
	}
	.when {
		flex: 0 0 132px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.what {
		flex: 1;
		min-width: 0;
	}
	.kind {
		font-weight: 600;
	}
	.mono {
		font-family: var(--font-mono);
	}
	.muted {
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.problem {
		color: var(--neg);
	}
	@media (max-width: 900px) {
		.entry {
			flex-direction: column;
			gap: 4px;
		}
	}
</style>
