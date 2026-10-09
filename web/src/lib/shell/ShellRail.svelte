<script lang="ts">
	/**
	 * Shell left rail (ADR 0079): the brand link, the four primary destinations,
	 * and the collapsible System group. The layout owns whether System is open.
	 */
	import { resolve } from '$app/paths';
	import {
		PRIMARY_NAV,
		SYSTEM_NAV,
		isNavHrefActive,
		isPrimaryNavActive,
		isSystemRoute,
		type PrimaryNavItem
	} from '$lib/workstation-chrome';

	let {
		routeId,
		systemOpen,
		ontogglesystem
	}: {
		routeId: string | null;
		/** The System group is expanded (stored by the viewer, or on a System route). */
		systemOpen: boolean;
		/** Expand or collapse the System group. */
		ontogglesystem: () => void;
	} = $props();

	const icons: Record<PrimaryNavItem['id'], string> = {
		home: 'M3 11l9-7 9 7M5 10v10h14V10',
		strategies: 'M4 19l5-6 4 3 7-9M4 5v14h16',
		portfolio: 'M21 12A9 9 0 1112 3v9zM15 3.5A9 9 0 0120.5 9H15z',
		trade: 'M7 7h11l-3-3M17 17H6l3 3'
	};
</script>

<aside class="rail">
	<a class="brand" href={resolve('/')} aria-label="ThyTrader, go to Home">
		<span class="mark" aria-hidden="true">T</span>
		<span class="brand-name">ThyTrader</span>
	</a>
	<nav class="rail-nav" aria-label="Primary navigation">
		{#each PRIMARY_NAV as item (item.id)}
			{@const active = isPrimaryNavActive(item, routeId)}
			<a
				href={resolve(item.href)}
				class="nav"
				class:on={active}
				aria-current={active ? 'page' : undefined}
			>
				<svg
					width="16"
					height="16"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="1.8"
					stroke-linecap="round"
					stroke-linejoin="round"
					aria-hidden="true"><path d={icons[item.id]}></path></svg
				>
				<span class="nav-label">{item.label}</span>
			</a>
		{/each}

		<div class="rail-spacer"></div>

		<button
			type="button"
			class="nav system-toggle"
			class:on={isSystemRoute(routeId) && !systemOpen}
			aria-expanded={systemOpen}
			aria-controls="rail-system-group"
			onclick={ontogglesystem}
		>
			<svg
				width="16"
				height="16"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="1.8"
				stroke-linecap="round"
				stroke-linejoin="round"
				aria-hidden="true"
				><circle cx="12" cy="12" r="3"></circle><path
					d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"
				></path></svg
			>
			<span class="nav-label">System</span>
			<svg
				class="chevron"
				class:open={systemOpen}
				width="12"
				height="12"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				aria-hidden="true"><path d="M6 15l6-6 6 6"></path></svg
			>
		</button>
		{#if systemOpen}
			<div id="rail-system-group" class="system-group">
				{#each SYSTEM_NAV as item (item.href)}
					{@const active = isNavHrefActive(item.href, routeId)}
					<a
						href={resolve(item.href)}
						class="nav sub"
						class:on={active}
						aria-current={active ? 'page' : undefined}
					>
						<span class="nav-label">{item.label}</span>
					</a>
				{/each}
			</div>
		{/if}
	</nav>
</aside>

<style>
	.rail {
		position: sticky;
		top: 0;
		flex: none;
		display: flex;
		flex-direction: column;
		width: var(--rail-width);
		height: 100vh;
		padding: 14px 10px;
		overflow-y: auto;
		background: var(--rail);
		border-right: 1px solid var(--line);
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 6px 10px 18px;
		color: var(--text);
		font-size: 15px;
		font-weight: 600;
		letter-spacing: -0.01em;
		text-decoration: none;
	}
	.mark {
		display: grid;
		place-items: center;
		width: 26px;
		height: 26px;
		border-radius: 7px;
		background: var(--accent);
		color: var(--accent-ink);
		font-size: 14px;
		font-weight: 700;
	}
	.rail-nav {
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: 4px;
	}
	.rail-spacer {
		flex-grow: 1;
	}
	.nav {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		min-height: 40px;
		padding: 0 10px;
		border: 0;
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--muted);
		font-weight: 500;
		text-align: left;
		text-decoration: none;
		cursor: pointer;
	}
	.nav:hover {
		background: var(--hover);
		color: var(--text);
	}
	.nav.on {
		background: var(--surface-2);
		color: var(--text);
		box-shadow: inset 0 0 0 1px var(--line);
	}
	.nav svg {
		flex: none;
	}
	.chevron {
		margin-left: auto;
		transform: rotate(180deg);
	}
	.chevron.open {
		transform: none;
	}
	.system-group {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.sub {
		min-height: 34px;
		padding-left: 36px;
		font-size: 12.5px;
	}

	@media (prefers-reduced-motion: no-preference) {
		.nav {
			transition:
				background 0.12s,
				color 0.12s;
		}
	}

	@media (max-width: 860px) {
		.rail {
			width: 64px;
			padding: 14px 8px;
		}
		.brand {
			padding: 6px 11px 18px;
		}
		.brand-name,
		.nav-label,
		.chevron {
			position: absolute;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip: rect(0 0 0 0);
			white-space: nowrap;
		}
		.nav {
			justify-content: center;
			min-height: 44px;
			padding: 0;
		}
		.sub {
			padding: 0;
		}
		.sub .nav-label {
			position: static;
			width: auto;
			height: auto;
			clip: auto;
			font-size: 10px;
			white-space: normal;
			text-align: center;
		}
	}
</style>
