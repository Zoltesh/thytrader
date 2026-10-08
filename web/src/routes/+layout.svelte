<script lang="ts">
	/**
	 * Workstation shell (ADR 0079): left rail with four destinations and a
	 * collapsible System group, a top bar (breadcrumb, command palette trigger,
	 * Agent toggle, theme toggle), the page content column, and the Agent side
	 * panel hosting the operator chat. Pages render their own <main>.
	 *
	 * The rail, top bar and Agent panel live in `$lib/shell/`; this layout owns
	 * their state (theme, panel and System group flags, the ⌘K chord).
	 */
	import '@fontsource-variable/geist';
	import '@fontsource-variable/geist-mono';
	import { page } from '$app/state';
	import { agentRequests } from '$lib/agent-request.svelte';
	import favicon from '$lib/assets/favicon.svg';
	import CommandPalette from '$lib/CommandPalette.svelte';
	import { liveChrome } from '$lib/live-context.svelte';
	import ShellAgentPanel from '$lib/shell/ShellAgentPanel.svelte';
	import ShellRail from '$lib/shell/ShellRail.svelte';
	import ShellTopBar from '$lib/shell/ShellTopBar.svelte';
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
	import { breadcrumbFor, isSystemRoute } from '$lib/workstation-chrome';
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
		agentRequests.clear();
		agentToggle?.focus();
	}

	// A page asked the agent something ("Ask why" on a proposal): open the panel.
	let answeredSequence = 0;
	$effect(() => {
		const request = agentRequests.current;
		if (request.sequence === answeredSequence) return;
		answeredSequence = request.sequence;
		if (request.draft !== null) void openAgent();
	});

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
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>ThyTrader</title>
	<meta name="description" content="A local-first Coinbase portfolio and strategy workstation." />
</svelte:head>

<svelte:window onkeydown={onWindowKeydown} />

<div class="app" class:agent-open={agentVisible} data-shell-hydrated={hydrated ? 'true' : 'false'}>
	<ShellRail {routeId} {systemOpen} ontogglesystem={toggleSystem} />

	<div class="main-col" class:live-frame={live !== null} data-live-frame={live !== null}>
		<ShellTopBar
			{crumb}
			{theme}
			{agentVisible}
			{onChatPage}
			{live}
			bind:agentToggle
			onsearch={() => (paletteOpen = true)}
			ontoggleagent={() => (agentOpen ? closeAgent() : void openAgent())}
			ontoggletheme={toggleTheme}
		/>
		<div class="content">
			{@render children()}
		</div>
	</div>

	{#if agentVisible}
		<ShellAgentPanel {routeId} bind:panel={agentPanel} onclose={closeAgent} />
	{/if}
</div>

<CommandPalette bind:open={paletteOpen} onOpenAgent={() => void openAgent()} />

<style>
	.app {
		display: flex;
		min-height: 100vh;
		background: var(--bg);
	}

	/* Main column */
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
	.content {
		flex: 1;
		min-width: 0;
	}

	@media (max-width: 860px) {
		.content :global(main) {
			padding: var(--space-5) var(--space-4) var(--space-8);
		}
	}
</style>
