<script lang="ts">
	/**
	 * Shell Agent side panel (ADR 0079): what the agent is looking at and the
	 * operator chat, prefilled by a page's agent request ("Ask why").
	 */
	import { agentRequests } from '$lib/agent-request.svelte';
	import OperatorChatPanel from '$lib/OperatorChatPanel.svelte';
	import { agentContextLabel } from '$lib/workstation-chrome';

	let {
		routeId,
		panel = $bindable(),
		onclose
	}: {
		routeId: string | null;
		/** The panel element, so opening it can move focus into it. */
		panel?: HTMLElement;
		/** Close the panel and return focus to the Agent toggle. */
		onclose: () => void;
	} = $props();
</script>

<aside
	id="agent-panel"
	class="agent"
	aria-labelledby="agent-panel-title"
	tabindex="-1"
	bind:this={panel}
>
	<div class="agent-head">
		<h2 id="agent-panel-title">Agent</h2>
		<span class="chip">Same gates as the CLI</span>
		<button type="button" class="btn ghost icon" aria-label="Close agent" onclick={onclose}
			>✕</button
		>
	</div>
	<p class="agent-context" data-testid="agent-context">
		Looking at: <b>{agentRequests.current.context ?? agentContextLabel(routeId)}</b>
	</p>
	<div class="agent-body">
		<OperatorChatPanel
			variant="panel"
			prefill={agentRequests.current.draft}
			prefillSequence={agentRequests.current.sequence}
		/>
	</div>
</aside>

<style>
	.icon {
		width: 34px;
		padding: 0;
	}
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

	/* Narrow desktop: the agent panel becomes an overlay. */
	@media (max-width: 1100px) {
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
</style>
