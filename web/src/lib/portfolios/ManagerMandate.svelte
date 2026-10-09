<script lang="ts">
	/**
	 * Manager mandate and permissions (ADR 0091): what the manager agent should
	 * aim for, what it may do on its own, and what it may never do, viewed and
	 * edited in place. Saving journals the change; the Manager tab reloads the
	 * journal through `onsaved`.
	 */
	import { compareDecimalStrings } from '$lib/portfolio';
	import {
		CONFLICT_RELOADED,
		MANAGER_NEVER,
		MANAGER_NOTE,
		errorText,
		fractionToPercentInput,
		isRevisionConflict,
		percentInputToFraction,
		updatePortfolio,
		weightPercent,
		type ManagerSettings,
		type Portfolio
	} from '$lib/portfolios';

	let {
		portfolio,
		notice = $bindable(),
		onchanged,
		onconflict,
		onsaved
	}: {
		portfolio: Portfolio;
		notice: string | null;
		onchanged: (portfolio: Portfolio) => void;
		onconflict: () => Promise<void>;
		/** After the settings were saved (reload the journal). */
		onsaved: () => Promise<void>;
	} = $props();

	let editing = $state(false);
	let mandate = $state('');
	let mayRebalance = $state(false);
	let weeklyChange = $state('');
	let mayPause = $state(false);
	let mayPropose = $state(false);
	let saving = $state(false);
	let error = $state<string | null>(null);

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
			await onsaved();
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
</script>

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

<style>
	.body {
		padding: 16px 18px;
	}
	.head {
		display: flex;
		align-items: center;
		gap: 8px;
	}
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
	.muted {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.problem {
		color: var(--neg);
	}
</style>
