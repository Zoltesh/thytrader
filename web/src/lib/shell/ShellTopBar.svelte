<script lang="ts">
	/**
	 * Shell top bar (ADR 0079): breadcrumb, command palette trigger, Agent toggle,
	 * theme toggle, and the polite live region holding the LIVE strip. The layout
	 * owns the palette, agent and theme state; this component only renders it.
	 */
	import { liveStripText, type LiveContext } from '$lib/live-context';
	import type { Theme } from '$lib/theme';
	import type { Breadcrumb } from '$lib/workstation-chrome';

	let {
		crumb,
		theme,
		agentVisible,
		onChatPage,
		live,
		agentToggle = $bindable(),
		onsearch,
		ontoggleagent,
		ontoggletheme
	}: {
		crumb: Breadcrumb;
		theme: Theme;
		/** The Agent side panel is showing. */
		agentVisible: boolean;
		/** The operator chat route hosts the agent full-page; the toggle is disabled there. */
		onChatPage: boolean;
		/** Declared live context of the current route, or null. */
		live: LiveContext | null;
		/** The Agent toggle button, so closing the panel can return focus to it. */
		agentToggle?: HTMLButtonElement;
		/** Open the command palette. */
		onsearch: () => void;
		/** Open or close the Agent side panel. */
		ontoggleagent: () => void;
		/** Switch between the dark and light themes. */
		ontoggletheme: () => void;
	} = $props();
</script>

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
			onclick={onsearch}
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
			onclick={ontoggleagent}
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
			onclick={ontoggletheme}
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

<style>
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

	@media (prefers-reduced-motion: no-preference) {
		.live-strip {
			animation: live-strip-in 0.16s ease-out;
		}
	}
	@keyframes live-strip-in {
		from {
			opacity: 0;
		}
	}

	/* Narrow desktop: compact search. */
	@media (max-width: 1100px) {
		.search {
			width: 44px;
			justify-content: center;
		}
		.search-text,
		.search .kbd {
			display: none;
		}
	}
	@media (max-width: 860px) {
		.top {
			padding: 0 var(--space-3);
		}
		.live-strip {
			padding: 6px var(--space-3);
		}
	}
</style>
