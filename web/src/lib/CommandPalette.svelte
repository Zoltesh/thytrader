<script lang="ts">
	/**
	 * Command palette (ADR 0079): ⌘K / Ctrl+K or the top-bar search trigger.
	 *
	 * A modal <dialog> (focus stays inside, Escape closes) holding a combobox
	 * over a listbox. Arrow keys move the active option, Enter runs it, and
	 * focus returns to whatever opened the palette. It only navigates or opens
	 * the Agent panel; it never runs a mutation.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { tick } from 'svelte';
	import {
		PALETTE_COMMANDS,
		filterPaletteCommands,
		type PaletteCommand
	} from '$lib/workstation-chrome';

	let {
		open = $bindable(false),
		onOpenAgent
	}: {
		open?: boolean;
		onOpenAgent: () => void;
	} = $props();

	const uid = $props.id();
	const listId = `palette-list-${uid}`;
	const optionId = (index: number): string => `palette-opt-${uid}-${index}`;

	let dialog: HTMLDialogElement | undefined = $state();
	let input: HTMLInputElement | undefined = $state();
	let query = $state('');
	let active = $state(0);
	let returnFocus: HTMLElement | null = null;

	const results = $derived(filterPaletteCommands(PALETTE_COMMANDS, query));
	const groups = $derived.by(() => {
		const out: { name: string; items: { command: PaletteCommand; index: number }[] }[] = [];
		results.forEach((command, index) => {
			let group = out.find((entry) => entry.name === command.group);
			if (group === undefined) {
				group = { name: command.group, items: [] };
				out.push(group);
			}
			group.items.push({ command, index });
		});
		return out;
	});

	$effect(() => {
		const el = dialog;
		if (el === undefined) return;
		if (open && !el.open) {
			returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
			query = '';
			active = 0;
			el.showModal();
			void tick().then(() => input?.focus());
		} else if (!open && el.open) {
			el.close();
		}
	});

	function handleClose(): void {
		open = false;
		const target = returnFocus;
		returnFocus = null;
		if (target !== null && target.isConnected) {
			target.focus();
		}
	}

	function scrollActiveIntoView(): void {
		void tick().then(() => {
			document.getElementById(optionId(active))?.scrollIntoView({ block: 'nearest' });
		});
	}

	async function run(command: PaletteCommand | undefined): Promise<void> {
		if (command === undefined) return;
		if (command.kind === 'agent') {
			returnFocus = null;
			open = false;
			onOpenAgent();
			return;
		}
		returnFocus = null;
		open = false;
		await goto(resolve(command.href as '/'));
	}

	function onInputKeydown(event: KeyboardEvent): void {
		const count = results.length;
		if (event.key === 'ArrowDown') {
			event.preventDefault();
			if (count > 0) active = (active + 1) % count;
			scrollActiveIntoView();
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			if (count > 0) active = (active - 1 + count) % count;
			scrollActiveIntoView();
		} else if (event.key === 'Home') {
			event.preventDefault();
			active = 0;
			scrollActiveIntoView();
		} else if (event.key === 'End') {
			event.preventDefault();
			active = Math.max(0, count - 1);
			scrollActiveIntoView();
		} else if (event.key === 'Enter') {
			event.preventDefault();
			void run(results[active]);
		}
	}
</script>

<dialog
	bind:this={dialog}
	class="palette"
	aria-label="Command palette"
	onclose={handleClose}
	onclick={(event) => {
		// A click on the backdrop lands on the <dialog> element itself.
		if (event.target === dialog) open = false;
	}}
>
	<div class="palette-box">
		<div class="palette-search">
			<svg
				width="16"
				height="16"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				aria-hidden="true"
				><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.5-3.5"></path></svg
			>
			<input
				bind:this={input}
				bind:value={query}
				oninput={() => (active = 0)}
				onkeydown={onInputKeydown}
				type="text"
				role="combobox"
				aria-label="Search pages and actions"
				aria-expanded="true"
				aria-controls={listId}
				aria-autocomplete="list"
				aria-activedescendant={results.length > 0 ? optionId(active) : undefined}
				placeholder="Jump to a page or action…"
				autocomplete="off"
				spellcheck="false"
			/>
			<span class="kbd" aria-hidden="true">esc</span>
		</div>
		<ul id={listId} class="palette-list" role="listbox" aria-label="Pages and actions">
			{#each groups as group (group.name)}
				<li role="presentation" class="palette-group">{group.name}</li>
				{#each group.items as { command, index } (command.id)}
					<li
						id={optionId(index)}
						role="option"
						aria-selected={index === active}
						class="palette-row"
						class:sel={index === active}
						onmousemove={() => (active = index)}
						onclick={() => void run(command)}
						onkeydown={() => undefined}
					>
						<span>{command.label}</span>
						{#if command.hint}<span class="hint">{command.hint}</span>{/if}
					</li>
				{/each}
			{:else}
				<li role="presentation" class="palette-empty">No pages or actions match “{query}”.</li>
			{/each}
		</ul>
	</div>
</dialog>

<style>
	.palette {
		width: min(580px, calc(100vw - 32px));
		max-height: min(520px, calc(100vh - 140px));
		margin: 110px auto auto;
		padding: 0;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-xl);
		background: var(--surface);
		color: var(--text);
		box-shadow: var(--shadow);
		overflow: hidden;
	}
	.palette::backdrop {
		background: var(--scrim);
	}
	.palette-box {
		display: flex;
		flex-direction: column;
		max-height: inherit;
	}
	.palette-search {
		display: flex;
		align-items: center;
		gap: 10px;
		height: 50px;
		padding: 0 14px;
		border-bottom: 1px solid var(--line);
		color: var(--muted);
	}
	.palette-search input {
		flex: 1;
		min-width: 0;
		height: 100%;
		border: 0;
		outline: none;
		background: transparent;
		color: var(--text);
		font-size: var(--fs-md);
	}
	.palette-search input::placeholder {
		color: var(--faint);
	}
	.palette-list {
		list-style: none;
		margin: 0;
		padding: 6px 0;
		overflow-y: auto;
	}
	.palette-group {
		padding: 10px 14px 4px;
		color: var(--faint);
		font-size: var(--fs-xs);
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}
	.palette-row {
		display: flex;
		align-items: center;
		gap: 10px;
		min-height: 38px;
		padding: 0 14px;
		cursor: pointer;
	}
	.palette-row.sel {
		background: var(--hover);
		box-shadow: inset 2px 0 0 var(--accent);
	}
	.hint {
		margin-left: auto;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.palette-empty {
		padding: 14px;
		color: var(--muted);
	}
</style>
