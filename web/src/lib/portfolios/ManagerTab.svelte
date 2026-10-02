<script lang="ts">
	/**
	 * Manager tab (ADR 0091): the manager agent's proposals (Approve / Decline /
	 * Ask why), its editable mandate and permissions, and the journal.
	 *
	 * The manager agent runs outside ThyTrader and only proposes: rebalance,
	 * pause or resume a sleeve, add a sleeve. Inside its permissions a proposal
	 * applies on its own; everything else waits here. It can never place
	 * orders: strategies place every trade. "Ask why" opens the Agent panel with
	 * the proposal as context. The journal is append-only and newest first.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { askAgent } from '$lib/agent-request.svelte';
	import { compareDecimalStrings } from '$lib/portfolio';
	import {
		CONFLICT_RELOADED,
		MANAGER_NEVER,
		MANAGER_NOTE,
		approvalNeedsLiveAck,
		askWhyPrompt,
		decideProposal,
		errorText,
		fractionToPercentInput,
		isRevisionConflict,
		journalActorText,
		journalKindLabel,
		listJournal,
		listProposals,
		percentInputToFraction,
		proposalKindLabel,
		proposalStatusText,
		updatePortfolio,
		utcMinute,
		weightPercent,
		type JournalEntry,
		type ManagerSettings,
		type Portfolio,
		type Proposal
	} from '$lib/portfolios';

	let {
		portfolio,
		onchanged,
		onconflict,
		ondecided = async () => {}
	}: {
		portfolio: Portfolio;
		onchanged: (portfolio: Portfolio) => void;
		onconflict: () => Promise<void>;
		/** After an approval or decline changed the portfolio (reload it). */
		ondecided?: () => Promise<void>;
	} = $props();

	let proposals = $state<Proposal[]>([]);
	let proposalsError = $state<string | null>(null);
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

	async function loadProposals(): Promise<void> {
		proposalsError = null;
		try {
			proposals = await listProposals(portfolio.portfolio_id, null, 20);
		} catch (caught) {
			proposalsError = errorText(caught, 'Proposals are unavailable.');
		}
	}

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
			notice =
				decision === 'approve' ? `Approved: ${proposal.summary}` : `Declined: ${proposal.summary}`;
			await ondecided();
			await Promise.all([loadProposals(), loadJournal(true)]);
		} catch (caught) {
			decisionError = errorText(caught, 'The decision could not be recorded.');
			await loadProposals();
		} finally {
			deciding = false;
		}
	}

	function askWhy(proposal: Proposal): void {
		askAgent(`Proposal: ${proposal.summary}`, askWhyPrompt(portfolio, proposal));
	}

	let editing = $state(false);
	let mandate = $state('');
	let mayRebalance = $state(false);
	let weeklyChange = $state('');
	let mayPause = $state(false);
	let mayPropose = $state(false);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let notice = $state<string | null>(null);

	let entries = $state<JournalEntry[]>([]);
	let nextCursor = $state<string | null>(null);
	let journalLoading = $state(false);
	let journalError = $state<string | null>(null);

	const manager = $derived(portfolio.manager);
	const permissions = $derived(manager.permissions);

	const draft = $derived.by((): { manager: ManagerSettings | null; problem: string | null } => {
		const change = percentInputToFraction(weeklyChange);
		if (
			change === null ||
			compareDecimalStrings(change, '0') <= 0 ||
			compareDecimalStrings(change, '1') > 0
		) {
			return {
				manager: null,
				problem: 'Max weight change per week is a percent above 0, at most 100.'
			};
		}
		if (mandate.trim().length > 2000) {
			return { manager: null, problem: 'The mandate allows at most 2,000 characters.' };
		}
		return {
			manager: {
				mandate: mandate.trim(),
				permissions: {
					may_rebalance: mayRebalance,
					max_weight_change_per_week: change,
					may_pause_sleeves: mayPause,
					may_propose_sleeves: mayPropose
				}
			},
			problem: null
		};
	});

	function startEditing(): void {
		mandate = manager.mandate;
		mayRebalance = permissions.may_rebalance;
		weeklyChange = fractionToPercentInput(permissions.max_weight_change_per_week);
		mayPause = permissions.may_pause_sleeves;
		mayPropose = permissions.may_propose_sleeves;
		error = null;
		notice = null;
		editing = true;
	}

	async function save(): Promise<void> {
		const next = draft.manager;
		if (next === null) return;
		saving = true;
		error = null;
		try {
			const updated = await updatePortfolio(portfolio.portfolio_id, {
				revision: portfolio.revision,
				manager: next
			});
			onchanged(updated);
			editing = false;
			notice = 'Manager settings saved.';
			await loadJournal(true);
		} catch (caught) {
			if (isRevisionConflict(caught)) {
				await onconflict();
				editing = false;
				error = CONFLICT_RELOADED;
			} else {
				error = errorText(caught, 'The manager settings could not be saved.');
			}
		} finally {
			saving = false;
		}
	}

	async function loadJournal(reset: boolean): Promise<void> {
		journalLoading = true;
		journalError = null;
		try {
			const page = await listJournal(portfolio.portfolio_id, reset ? null : nextCursor);
			entries = reset ? page.entries : [...entries, ...page.entries];
			nextCursor = page.has_more ? page.next_cursor : null;
		} catch (caught) {
			journalError = errorText(caught, 'The journal is unavailable.');
		} finally {
			journalLoading = false;
		}
	}

	let loadedFor = '';
	$effect(() => {
		const key = `${portfolio.portfolio_id}:${portfolio.revision}`;
		if (key === loadedFor) return;
		loadedFor = key;
		void loadJournal(true);
		void loadProposals();
	});
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

<div class="layout">
	<section class="card body" aria-label="Manager mandate and permissions">
		<div class="head">
			<h2>Mandate</h2>
			{#if !editing}
				<button type="button" class="btn ghost compact" onclick={startEditing}>Edit</button>
			{/if}
		</div>
		<p class="note" data-testid="manager-note">{MANAGER_NOTE}</p>
		{#if editing}
			<label class="field">
				<span>Mandate (what the manager should aim for)</span>
				<textarea rows="5" bind:value={mandate} maxlength="2000"></textarea>
			</label>
			<fieldset class="permissions">
				<legend>The manager may</legend>
				<label class="toggle"
					><input type="checkbox" bind:checked={mayRebalance} /> Rebalance sleeve weights</label
				>
				<label class="field inline">
					<span>Max weight change per week (%)</span>
					<input type="text" inputmode="decimal" bind:value={weeklyChange} />
				</label>
				<label class="toggle"
					><input type="checkbox" bind:checked={mayPause} /> Pause a sleeve that breaks its evidence</label
				>
				<label class="toggle"
					><input type="checkbox" bind:checked={mayPropose} /> Run research and propose new sleeves</label
				>
			</fieldset>
			<div class="actions">
				{#if draft.problem}<span class="problem small" role="status">{draft.problem}</span>{/if}
				<span class="spacer"></span>
				<button type="button" class="btn" onclick={() => (editing = false)} disabled={saving}
					>Cancel</button
				>
				<button
					type="button"
					class="btn primary"
					disabled={draft.manager === null || saving}
					onclick={() => void save()}>{saving ? 'Saving…' : 'Save manager settings'}</button
				>
			</div>
		{:else}
			<p class="mandate" data-testid="manager-mandate">
				{manager.mandate === '' ? 'No mandate yet.' : manager.mandate}
			</p>
			<h3>The manager may</h3>
			<ul class="rules" data-testid="manager-may">
				<li class:off={!permissions.may_rebalance}>
					<span aria-hidden="true">{permissions.may_rebalance ? '✓' : '–'}</span>
					<span
						>Rebalance sleeve weights{permissions.may_rebalance
							? ` by up to ${weightPercent(permissions.max_weight_change_per_week)} per week`
							: ' (off: every rebalance waits for you)'}{permissions.may_rebalance
							? portfolio.mode === 'live'
								? ' — on this live portfolio each rebalance still waits for you'
								: ' — applied on its own inside that budget'
							: ''}</span
					>
				</li>
				<li class:off={!permissions.may_pause_sleeves}>
					<span aria-hidden="true">{permissions.may_pause_sleeves ? '✓' : '–'}</span>
					Pause a sleeve that breaks its evidence{permissions.may_pause_sleeves
						? ' — applied on its own'
						: ' (off: pauses wait for you)'}
				</li>
				<li class:off={!permissions.may_propose_sleeves}>
					<span aria-hidden="true">{permissions.may_propose_sleeves ? '✓' : '–'}</span>
					Run research and propose new sleeves{permissions.may_propose_sleeves
						? ' — each waits for you'
						: ' (off)'}
				</li>
			</ul>
		{/if}
		<h3>It may never</h3>
		<ul class="rules never" data-testid="manager-never">
			{#each MANAGER_NEVER as rule (rule)}
				<li><span aria-hidden="true">✕</span> {rule}</li>
			{/each}
		</ul>
		{#if error}<p class="problem small" role="alert">{error}</p>{/if}
		{#if notice}<p class="muted small" role="status">{notice}</p>{/if}
	</section>
	<section class="card" aria-label="Portfolio journal">
		<div class="journal-head">
			<h2>Journal</h2>
			<span class="muted small">Append-only · newest first</span>
		</div>
		{#if journalError}
			<p class="problem small body-pad" role="alert">{journalError}</p>
		{:else if entries.length === 0 && !journalLoading}
			<p class="muted body-pad">Nothing recorded yet.</p>
		{/if}
		<ol class="journal" data-testid="portfolio-journal">
			{#each entries as entry (entry.entry_id)}
				<li class="entry" data-kind={entry.kind}>
					<div class="when mono">{utcMinute(entry.occurred_at)}</div>
					<div class="what">
						<div class="kind">{journalKindLabel(entry.kind)}</div>
						<div>{entry.summary}</div>
						<div class="faint small">{journalActorText(entry)} · revision {entry.revision}</div>
					</div>
				</li>
			{/each}
		</ol>
		{#if nextCursor !== null}
			<div class="body-pad">
				<button
					type="button"
					class="btn ghost"
					disabled={journalLoading}
					onclick={() => void loadJournal(false)}
					>{journalLoading ? 'Loading…' : 'Load more'}</button
				>
			</div>
		{/if}
	</section>
</div>

<style>
	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr);
		gap: 16px;
		align-items: start;
	}
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
	.body {
		padding: 16px 18px;
	}
	.body-pad {
		padding: 12px 18px;
	}
	.head,
	.journal-head {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.journal-head {
		padding: 14px 18px;
		border-bottom: 1px solid var(--line);
	}
	.journal-head .muted,
	.head .btn {
		margin-left: auto;
	}
	.compact {
		min-height: 28px;
		padding: 0 8px;
	}
	h3 {
		margin: 16px 0 6px;
		color: var(--faint);
		font-size: var(--fs-sm);
		font-weight: 500;
	}
	.note {
		margin: 8px 0 0;
		padding: 10px 12px;
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.mandate {
		margin: 12px 0 0;
		white-space: pre-line;
		color: var(--muted);
	}
	.rules {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.rules li {
		display: flex;
		gap: 10px;
		padding: 7px 0;
		border-top: 1px solid var(--line);
	}
	.rules li.off {
		color: var(--faint);
	}
	.never span {
		color: var(--neg);
	}
	.field {
		display: grid;
		gap: 6px;
		margin-top: 12px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.field.inline {
		margin: 4px 0 4px 24px;
	}
	.field input,
	.field textarea {
		padding: 6px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	.permissions {
		display: grid;
		gap: 6px;
		margin: 12px 0 0;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
	}
	.permissions legend {
		padding: 0 4px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-top: 12px;
	}
	.spacer {
		flex: 1;
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
		.layout {
			grid-template-columns: minmax(0, 1fr);
		}
		.entry {
			flex-direction: column;
			gap: 4px;
		}
	}
</style>
