<script lang="ts">
	/**
	 * Grouped, searchable indicator kind picker for the strategy builder.
	 *
	 * A button shows the current kind; it opens a popover holding a search
	 * combobox over a listbox grouped by category (Trend, Momentum, Volatility,
	 * Volume, Statistical, Price). Arrow keys, Home, and End move the active
	 * option, Enter picks it, Escape closes, and focus returns to the button.
	 * Clicking outside closes without moving focus. The catalog comes from the
	 * generated Python registry, so every implemented kind and only those appear.
	 */
	import { tick } from 'svelte';
	import {
		catalogEntry,
		findCatalogEntry,
		groupedIndicatorKinds,
		type IndicatorKindValue
	} from '$lib/indicator-catalog';

	let {
		kind,
		onselect,
		labelledby,
		disabled = false
	}: {
		/** The selected kind. */
		kind: IndicatorKindValue;
		/** Called with the newly chosen kind (not called when the choice is unchanged). */
		onselect: (kind: IndicatorKindValue) => void;
		/** Id of the visible field label, combined with the current kind as the button name. */
		labelledby?: string;
		disabled?: boolean;
	} = $props();

	const uid = $props.id();
	const triggerId = `kind-trigger-${uid}`;
	const panelId = `kind-panel-${uid}`;
	const listId = `kind-list-${uid}`;
	const optionId = (index: number): string => `kind-opt-${uid}-${index}`;
	const groupId = (category: string): string => `kind-group-${uid}-${category}`;

	let root: HTMLDivElement | undefined = $state();
	let trigger: HTMLButtonElement | undefined = $state();
	let input: HTMLInputElement | undefined = $state();
	let open = $state(false);
	let query = $state('');
	let active = $state(0);

	const current = $derived(findCatalogEntry(kind));
	const groups = $derived.by(() => {
		let index = 0;
		return groupedIndicatorKinds(query).map((group) => ({
			...group,
			items: group.kinds.map((item) => ({ kind: item, index: index++ }))
		}));
	});
	const flat = $derived(groups.flatMap((group) => group.items.map((item) => item.kind)));

	function scrollActiveIntoView(): void {
		void tick().then(() => {
			document.getElementById(optionId(active))?.scrollIntoView({ block: 'nearest' });
		});
	}

	function openPicker(): void {
		if (disabled || open) return;
		query = '';
		open = true;
		active = Math.max(
			0,
			groupedIndicatorKinds('')
				.flatMap((group) => group.kinds)
				.indexOf(kind)
		);
		void tick().then(() => {
			input?.focus();
			scrollActiveIntoView();
		});
	}

	function closePicker(returnFocus: boolean): void {
		if (!open) return;
		open = false;
		if (returnFocus) void tick().then(() => trigger?.focus());
	}

	function choose(next: IndicatorKindValue | undefined): void {
		if (next === undefined) return;
		closePicker(true);
		if (next !== kind) onselect(next);
	}

	function onTriggerKeydown(event: KeyboardEvent): void {
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			openPicker();
		}
	}

	function onInputKeydown(event: KeyboardEvent): void {
		const count = flat.length;
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
			choose(flat[active]);
		} else if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			closePicker(true);
		} else if (event.key === 'Tab') {
			closePicker(false);
		}
	}

	$effect(() => {
		if (!open) return;
		const onPointerDown = (event: PointerEvent): void => {
			if (root !== undefined && event.target instanceof Node && !root.contains(event.target)) {
				closePicker(false);
			}
		};
		document.addEventListener('pointerdown', onPointerDown, true);
		return () => document.removeEventListener('pointerdown', onPointerDown, true);
	});
</script>

<div class="kind-picker" bind:this={root}>
	<button
		bind:this={trigger}
		id={triggerId}
		class="kind-trigger"
		type="button"
		aria-haspopup="listbox"
		aria-expanded={open}
		aria-controls={open ? panelId : undefined}
		aria-labelledby={labelledby === undefined ? undefined : `${labelledby} ${triggerId}`}
		{disabled}
		title={current?.summary}
		onclick={() => (open ? closePicker(true) : openPicker())}
		onkeydown={onTriggerKeydown}
	>
		<span class="kind-name">{current?.label ?? kind}</span>
		<svg
			class="chevron"
			width="14"
			height="14"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			stroke-width="2"
			stroke-linecap="round"
			stroke-linejoin="round"
			aria-hidden="true"><path d="M6 9l6 6 6-6"></path></svg
		>
	</button>
	{#if open}
		<div id={panelId} class="kind-popover">
			<div class="kind-search">
				<svg
					width="15"
					height="15"
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
					aria-label="Search indicators"
					aria-expanded="true"
					aria-controls={listId}
					aria-autocomplete="list"
					aria-activedescendant={flat.length > 0 ? optionId(active) : undefined}
					placeholder="Search indicators (name, alias, or idea)…"
					autocomplete="off"
					spellcheck="false"
				/>
			</div>
			<div id={listId} class="kind-list" role="listbox" aria-label="Indicator kinds">
				{#each groups as group (group.category)}
					<div class="kind-group" role="group" aria-labelledby={groupId(group.category)}>
						<div id={groupId(group.category)} class="kind-group-label" role="presentation">
							{group.label}
						</div>
						{#each group.items as item (item.kind)}
							{@const entry = catalogEntry(item.kind)}
							<div
								id={optionId(item.index)}
								class="kind-option"
								class:active={item.index === active}
								class:current={item.kind === kind}
								role="option"
								tabindex="-1"
								aria-selected={item.kind === kind}
								onmousemove={() => (active = item.index)}
								onclick={() => choose(item.kind)}
								onkeydown={() => undefined}
							>
								<span class="option-label">{entry.label}</span>
								<span class="option-summary">{entry.summary}</span>
							</div>
						{/each}
					</div>
				{:else}
					<div class="kind-empty" role="presentation">No indicators match “{query}”.</div>
				{/each}
			</div>
		</div>
	{/if}
</div>

<style>
	.kind-picker {
		position: relative;
		min-width: 0;
	}
	.kind-trigger {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		min-height: 34px;
		padding: 6px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.kind-trigger:hover:not(:disabled) {
		border-color: var(--line-strong);
	}
	.kind-trigger:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 1px;
	}
	.kind-trigger:disabled {
		cursor: default;
	}
	.kind-name {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.chevron {
		flex: none;
		margin-left: auto;
		color: var(--muted);
	}
	.kind-trigger:disabled .chevron {
		display: none;
	}
	.kind-popover {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		z-index: 30;
		display: flex;
		flex-direction: column;
		width: max(100%, min(420px, calc(100vw - 32px)));
		max-height: 380px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-lg);
		background: var(--surface);
		box-shadow: var(--shadow);
		overflow: hidden;
	}
	.kind-search {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 0 12px;
		border-bottom: 1px solid var(--line);
		color: var(--muted);
	}
	.kind-search input {
		flex: 1;
		min-width: 0;
		height: 40px;
		padding: 0;
		border: 0;
		outline: none;
		background: transparent;
		color: var(--text);
		font: inherit;
		font-size: var(--fs-sm);
	}
	.kind-search input::placeholder {
		color: var(--faint);
	}
	.kind-list {
		padding: 4px 0 6px;
		overflow-y: auto;
	}
	.kind-group-label {
		padding: 8px 12px 3px;
		color: var(--faint);
		font-size: var(--fs-xs);
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}
	.kind-option {
		display: grid;
		gap: 1px;
		padding: 6px 12px;
		cursor: pointer;
	}
	.kind-option.active {
		background: var(--hover);
		box-shadow: inset 2px 0 0 var(--accent);
	}
	.option-label {
		color: var(--text);
		font-size: var(--fs-sm);
	}
	.kind-option.current .option-label {
		color: var(--accent);
		font-weight: 600;
	}
	.option-summary {
		color: var(--faint);
		font-size: var(--fs-xs);
		line-height: 1.35;
	}
	.kind-empty {
		padding: 12px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
</style>
