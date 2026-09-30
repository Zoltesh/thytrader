<script lang="ts">
	/**
	 * Full-page view of the operator chat (ADR 0051). The same component is the
	 * shell's Agent side panel (ADR 0079); this route stays for deep links.
	 */
	import OperatorChatPanel from '$lib/OperatorChatPanel.svelte';
	import PageHead from '$lib/PageHead.svelte';

	let chat: OperatorChatPanel | undefined = $state();
	let loading = $state(true);
</script>

<svelte:head>
	<title>Operator chat · ThyTrader</title>
</svelte:head>

<main>
	<PageHead eyebrow="In-app operator" title="Operator chat">
		{#snippet intro()}
			<p class="lede">
				Paste <strong>your LLM API key</strong>. This is not a Coinbase form — Coinbase keys stay on
				the separate secrets surface and never enter the browser. The chat uses the same gated skill
				lanes as <code>ops/</code>: operator is read-only; data, research, runtime, and memory
				mutations stay confirmation-gated. Live still needs the understand-live hard gate. The same
				chat is available on every page from the <strong>Agent</strong> button.
			</p>
		{/snippet}
		<button
			class="refresh"
			type="button"
			onclick={() => void chat?.loadTranscript()}
			disabled={loading}
		>
			{loading ? 'Refreshing…' : 'Refresh'}
		</button>
	</PageHead>

	<div class="chat-page">
		<OperatorChatPanel bind:this={chat} bind:loading variant="page" />
	</div>
</main>

<style>
	.chat-page {
		max-width: 60rem;
	}
</style>
