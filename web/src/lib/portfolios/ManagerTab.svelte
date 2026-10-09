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
	 *
	 * This component owns loading the proposals and the journal and the shared
	 * notice; the proposals, mandate and journal render in their own components.
	 */
	import {
		errorText,
		listJournal,
		listProposals,
		type JournalEntry,
		type Portfolio,
		type Proposal
	} from '$lib/portfolios';
	import ManagerJournal from './ManagerJournal.svelte';
	import ManagerMandate from './ManagerMandate.svelte';
	import ManagerProposals from './ManagerProposals.svelte';

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
	let notice = $state<string | null>(null);

	async function loadProposals(): Promise<void> {
		proposalsError = null;
		try {
			proposals = await listProposals(portfolio.portfolio_id, null, 20);
		} catch (caught) {
			proposalsError = errorText(caught, 'Proposals are unavailable.');
		}
	}

	async function decided(message: string): Promise<void> {
		notice = message;
		await ondecided();
		await Promise.all([loadProposals(), loadJournal(true)]);
	}

	let entries = $state<JournalEntry[]>([]);
	let nextCursor = $state<string | null>(null);
	let journalLoading = $state(false);
	let journalError = $state<string | null>(null);

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

<ManagerProposals
	{portfolio}
	{proposals}
	{proposalsError}
	ondecision={decided}
	onreload={loadProposals}
/>

<div class="layout">
	<ManagerMandate
		{portfolio}
		bind:notice
		{onchanged}
		{onconflict}
		onsaved={() => loadJournal(true)}
	/>
	<ManagerJournal
		{entries}
		{nextCursor}
		loading={journalLoading}
		error={journalError}
		onloadmore={() => void loadJournal(false)}
	/>
</div>

<style>
	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr);
		gap: 16px;
		align-items: start;
	}
	@media (max-width: 900px) {
		.layout {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
