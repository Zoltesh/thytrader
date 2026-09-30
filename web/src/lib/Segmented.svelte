<script lang="ts" generics="T extends string">
	/**
	 * Segmented control from the design reference (`.seg`).
	 *
	 * A labelled group of toggle buttons with `aria-pressed`; exactly one is
	 * pressed. `live` options take the amber treatment when selected, and
	 * their label still names the mode, so color is never the only signal.
	 */
	let {
		label,
		options,
		value,
		onchange,
		testId
	}: {
		/** Accessible name of the group. */
		label: string;
		options: readonly { id: T; label: string; live?: boolean; count?: number }[];
		value: T;
		onchange: (value: T) => void;
		testId?: string;
	} = $props();
</script>

<div class="seg" role="group" aria-label={label} data-testid={testId}>
	{#each options as option (option.id)}
		<button
			type="button"
			class:on={value === option.id && !option.live}
			class:on-live={value === option.id && option.live}
			aria-pressed={value === option.id}
			onclick={() => onchange(option.id)}
		>
			{option.label}{#if option.count !== undefined}<span class="count">{option.count}</span>{/if}
		</button>
	{/each}
</div>

<style>
	.seg {
		display: inline-flex;
		padding: 3px;
		border: 1px solid var(--line);
		border-radius: 9px;
		background: var(--surface-2);
	}
	button {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		min-height: 28px;
		padding: 0 14px;
		border: 0;
		border-radius: 7px;
		background: transparent;
		color: var(--muted);
		font-weight: 500;
		cursor: pointer;
	}
	button:hover {
		color: var(--text);
	}
	button.on {
		background: var(--surface);
		color: var(--text);
		box-shadow: 0 0 0 1px var(--line-2);
	}
	button.on-live {
		background: var(--live);
		color: var(--live-ink);
		font-weight: 600;
	}
	.count {
		color: inherit;
		font-size: var(--fs-xs);
		opacity: 0.75;
	}
	@media (prefers-reduced-motion: no-preference) {
		button {
			transition:
				background 0.12s,
				color 0.12s;
		}
	}
</style>
