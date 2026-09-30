<script lang="ts">
	/**
	 * Workstation shell (ADR 0079): left rail with four destinations and a
	 * collapsible System group, a top bar (breadcrumb, command palette trigger,
	 * Agent toggle, theme toggle), the page content column, and the Agent side
	 * panel hosting the operator chat. Pages render their own <main>.
	 */
	import '@fontsource-variable/geist';
	import '@fontsource-variable/geist-mono';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import favicon from '$lib/assets/favicon.svg';
	import CommandPalette from '$lib/CommandPalette.svelte';
	import { liveStripText } from '$lib/live-context';
	import { liveChrome } from '$lib/live-context.svelte';
	import OperatorChatPanel from '$lib/OperatorChatPanel.svelte';
	import {
		AGENT_PANEL_STORAGE_KEY,
		SYSTEM_GROUP_STORAGE_KEY,
		applyTheme,
		currentTheme,
		prefersLightScheme,
		readFlag,
		readStoredTheme,
		storeFlag,
		storeTheme,
		type Theme
	} from '$lib/theme';
	import {
		PRIMARY_NAV,
		SYSTEM_NAV,
		agentContextLabel,
		breadcrumbFor,
		isNavHrefActive,
		isPrimaryNavActive,
		isSystemRoute,
		type PrimaryNavItem
	} from '$lib/workstation-chrome';
	import { onMount, tick, type Snippet } from 'svelte';
	import '../app.css';

	let { children }: { children: Snippet } = $props();

	const routeId = $derived(page.route.id);
	const crumb = $derived(breadcrumbFor(routeId));
	const onChatPage = $derived(routeId === '/chat');

	let theme = $state<Theme>('dark');
	let agentOpen = $state(false);
	let systemStored = $state(false);
	let paletteOpen = $state(false);
	let hydrated = $state(false);
	let agentToggle: HTMLButtonElement | undefined = $state();
	let agentPanel: HTMLElement | undefined = $state();

	const systemOpen = $derived(systemStored || isSystemRoute(routeId));
	/** Declared by routes showing live exposure or composing a live order. */
	const live = $derived(liveChrome.current);
	const agentVisible = $derived(agentOpen && !onChatPage);

	onMount(() => {
		theme = currentTheme();
		agentOpen = readFlag(AGENT_PANEL_STORAGE_KEY, false);
		systemStored = readFlag(SYSTEM_GROUP_STORAGE_KEY, false);
		hydrated = true;
		let media: MediaQueryList | null = null;
		const followOs = (): void => {
			if (readStoredTheme() === null) {
				theme = prefersLightScheme() ? 'light' : 'dark';
				applyTheme(theme);
			}
		};
		try {
			media = window.matchMedia('(prefers-color-scheme: light)');
			media.addEventListener('change', followOs);
		} catch {
			media = null;
		}
		return () => media?.removeEventListener('change', followOs);
	});

	function toggleTheme(): void {
		theme = theme === 'dark' ? 'light' : 'dark';
		applyTheme(theme);
		storeTheme(theme);
	}

	function setAgent(open: boolean): void {
		agentOpen = open;
		storeFlag(AGENT_PANEL_STORAGE_KEY, open);
	}

	async function openAgent(): Promise<void> {
		setAgent(true);
		await tick();
		agentPanel?.focus();
	}

	function closeAgent(): void {
		setAgent(false);
		agentToggle?.focus();
	}

	function toggleSystem(): void {
		systemStored = !systemOpen;
		storeFlag(SYSTEM_GROUP_STORAGE_KEY, systemStored);
	}

	function onWindowKeydown(event: KeyboardEvent): void {
		// Only the ⌘K / Ctrl+K chord is intercepted; ordinary typing is never swallowed.
		if (event.key.toLowerCase() !== 'k' || event.altKey || event.shiftKey) return;
		if (!(event.metaKey || event.ctrlKey)) return;
		const target = event.target;
		const editable =
			target instanceof HTMLElement &&
			(target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
		const mac = /Mac|iPhone|iPad/.test(navigator.platform);
		// On macOS, Ctrl+K in a text field is the "kill line" editing shortcut.
		if (editable && mac && event.ctrlKey && !event.metaKey) return;
		event.preventDefault();
		paletteOpen = !paletteOpen;
	}

	const icons: Record<PrimaryNavItem['id'], string> = {
		home: 'M3 11l9-7 9 7M5 10v10h14V10',
		strategies: 'M4 19l5-6 4 3 7-9M4 5v14h16',
		portfolio: 'M21 12A9 9 0 1112 3v9zM15 3.5A9 9 0 0120.5 9H15z',
		trade: 'M7 7h11l-3-3M17 17H6l3 3'
	};
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>ThyTrader</title>
	<meta name="description" content="A local-first Coinbase portfolio and strategy workstation." />
</svelte:head>

<svelte:window onkeydown={onWindowKeydown} />

<div class="app" class:agent-open={agentVisible} data-shell-hydrated={hydrated ? 'true' : 'false'}>
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
				onclick={toggleSystem}
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

	<div class="main-col" class:live-frame={live !== null} data-live-frame={live !== null}>
		<div class="top-stack">
			<header class="top">
				<p class="crumb" data-testid="breadcrumb">
					{#if crumb.section}<span class="crumb-section">{crumb.section}</span><span
							class="crumb-sep"
							aria-hidden="true">/</span
						>{/if}<b>{crumb.page}</b>
				</p>
				<button
					type="button"
					class="search"
					aria-haspopup="dialog"
					aria-label="Search pages and actions"
					aria-keyshortcuts="Meta+K Control+K"
					onclick={() => (paletteOpen = true)}
				>
					<svg
						width="14"
						height="14"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						aria-hidden="true"
						><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.5-3.5"></path></svg
					>
					<span class="search-text">Jump to a page or action</span>
					<span class="kbd" aria-hidden="true">⌘K</span>
				</button>
				<button
					bind:this={agentToggle}
					type="button"
					class="btn"
					class:on={agentVisible}
					aria-pressed={agentVisible}
					aria-controls="agent-panel"
					disabled={onChatPage}
					title={onChatPage ? 'The agent is open full-page here' : undefined}
					onclick={() => (agentOpen ? closeAgent() : void openAgent())}
				>
					<svg
						width="15"
						height="15"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="1.8"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
						><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z"></path><path
							d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"
						></path></svg
					>
					Agent
				</button>
				<button
					type="button"
					class="btn ghost icon"
					aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
					data-testid="theme-toggle"
					onclick={toggleTheme}
				>
					{#if theme === 'dark'}
						<svg
							width="15"
							height="15"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
							stroke-linecap="round"
							aria-hidden="true"><path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"></path></svg
						>
					{:else}
						<svg
							width="15"
							height="15"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
							stroke-linecap="round"
							aria-hidden="true"
							><circle cx="12" cy="12" r="4"></circle><path
								d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
							></path></svg
						>
					{/if}
				</button>
			</header>
			<!-- A mounted polite live region, so screen readers announce the strip appearing. -->
			<div aria-live="polite">
				{#if live !== null}
					<div class="live-strip" data-testid="live-strip">
						<svg
							width="14"
							height="14"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2.2"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
							><path d="M12 9v4"></path><path d="M12 17h.01"></path><path
								d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"
							></path></svg
						>
						<span><b>LIVE:</b> {liveStripText(live)}</span>
					</div>
				{/if}
			</div>
		</div>
		<div class="content">
			{@render children()}
		</div>
	</div>

	{#if agentVisible}
		<aside
			id="agent-panel"
			class="agent"
			aria-labelledby="agent-panel-title"
			tabindex="-1"
			bind:this={agentPanel}
		>
			<div class="agent-head">
				<h2 id="agent-panel-title">Agent</h2>
				<span class="chip">Same gates as the CLI</span>
				<button type="button" class="btn ghost icon" aria-label="Close agent" onclick={closeAgent}
					>✕</button
				>
			</div>
			<p class="agent-context" data-testid="agent-context">
				Looking at: <b>{agentContextLabel(routeId)}</b>
			</p>
			<div class="agent-body">
				<OperatorChatPanel variant="panel" />
			</div>
		</aside>
	{/if}
</div>

<CommandPalette bind:open={paletteOpen} onOpenAgent={() => void openAgent()} />

<style>
	.app {
		display: flex;
		min-height: 100vh;
		background: var(--bg);
	}

	/* Rail */
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

	/* Main column and top bar */
	.main-col {
		position: relative;
		display: flex;
		flex: 1;
		flex-direction: column;
		min-width: 0;
	}
	/* Inset live frame: an overlay so sticky chrome and cards cannot cover it. */
	.main-col.live-frame::after {
		content: '';
		position: absolute;
		inset: 0;
		z-index: 25;
		box-shadow: inset 0 0 0 2px var(--live);
		pointer-events: none;
	}
	.top-stack {
		position: sticky;
		top: 0;
		z-index: 20;
	}
	.live-strip {
		display: flex;
		align-items: center;
		gap: 10px;
		min-height: 34px;
		padding: 6px var(--space-5);
		background: var(--live);
		color: var(--live-ink);
		font-size: 12.5px;
		font-weight: 600;
	}
	.live-strip svg {
		flex: none;
	}
	.live-strip b {
		font-weight: 700;
		letter-spacing: 0.03em;
	}
	.top {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		height: var(--topbar-height);
		padding: 0 var(--space-5);
		border-bottom: 1px solid var(--line);
		background: var(--bg);
	}
	.crumb {
		display: flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
		margin: 0;
		color: var(--muted);
		font-weight: 500;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.crumb b {
		color: var(--text);
		font-weight: 600;
	}
	.crumb-sep {
		color: var(--faint);
	}
	.search {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		width: 300px;
		min-width: 0;
		height: 34px;
		margin-left: auto;
		padding: 0 10px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--faint);
		cursor: pointer;
	}
	.search:hover {
		border-color: var(--line-2);
	}
	.search .kbd {
		margin-left: auto;
	}
	.search-text {
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}
	.icon {
		width: 34px;
		padding: 0;
	}
	.content {
		flex: 1;
		min-width: 0;
	}

	/* Agent side panel */
	.agent {
		position: sticky;
		top: 0;
		flex: none;
		display: flex;
		flex-direction: column;
		width: var(--agent-width);
		height: 100vh;
		border-left: 1px solid var(--line);
		background: var(--rail);
	}
	.agent:focus {
		outline: none;
	}
	.agent:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: -2px;
	}
	.agent-head {
		display: flex;
		flex: none;
		align-items: center;
		gap: var(--space-2);
		height: var(--topbar-height);
		padding: 0 14px;
		border-bottom: 1px solid var(--line);
	}
	.agent-head .chip {
		margin-left: auto;
	}
	.agent-context {
		flex: none;
		margin: 0;
		padding: 10px 14px;
		border-bottom: 1px solid var(--line);
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.agent-context b {
		color: var(--text);
	}
	.agent-body {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
	}

	@media (prefers-reduced-motion: no-preference) {
		.nav {
			transition:
				background 0.12s,
				color 0.12s;
		}
		.live-strip {
			animation: live-strip-in 0.16s ease-out;
		}
	}
	@keyframes live-strip-in {
		from {
			opacity: 0;
		}
	}

	/* Narrow desktop: icon rail, compact search, agent as an overlay. */
	@media (max-width: 1100px) {
		.search {
			width: 44px;
			justify-content: center;
		}
		.search-text,
		.search .kbd {
			display: none;
		}
		.agent {
			position: fixed;
			top: 0;
			right: 0;
			bottom: 0;
			z-index: 30;
			width: min(var(--agent-width), 100vw);
			box-shadow: var(--shadow);
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
		.top {
			padding: 0 var(--space-3);
		}
		.live-strip {
			padding: 6px var(--space-3);
		}
		.content :global(main) {
			padding: var(--space-5) var(--space-4) var(--space-8);
		}
	}
</style>
