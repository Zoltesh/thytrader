<script lang="ts">
	import PageHead from '$lib/PageHead.svelte';
	import CoinbaseCredentialsPanel from '$lib/CoinbaseCredentialsPanel.svelte';
	import YamlSettingsPanel from '$lib/YamlSettingsPanel.svelte';
</script>

<svelte:head>
	<title>Settings · ThyTrader</title>
</svelte:head>

<main>
	<PageHead eyebrow="Loopback settings" title="Settings">
		{#snippet intro()}
			<p class="lede">
				YAML is the source of truth for non-secret knobs, including YOLO. Coinbase Advanced Trade
				secrets are write-only beside that panel. LLM keys stay in the Agent panel. Extra exchanges
				are out of scope.
			</p>
		{/snippet}
	</PageHead>
	<YamlSettingsPanel />
	<CoinbaseCredentialsPanel />
</main>

<style>
</style>
