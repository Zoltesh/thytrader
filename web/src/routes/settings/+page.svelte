<script lang="ts">
	import YamlSettingsPanel from '$lib/YamlSettingsPanel.svelte';
</script>

<svelte:head>
	<title>Settings · ThyTrader</title>
</svelte:head>

<main>
	<section class="hero">
		<div>
			<p class="eyebrow">Loopback settings</p>
			<h1>Settings</h1>
			<p class="lede">
				YAML is the source of truth for non-secret knobs, including YOLO. Coinbase secrets stay
				server-side and are not on this form — a sibling workstation-IA slice owns that write-only
				panel. LLM keys stay on Chat. Extra exchanges are out of scope.
			</p>
		</div>
	</section>
	<YamlSettingsPanel />
</main>

<style>
	.hero {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 1rem;
		margin-bottom: 1.5rem;
	}
	.eyebrow {
		font-size: 0.8125rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: #a0aec0;
		margin: 0 0 0.25rem 0;
	}
	h1 {
		font-size: 2rem;
		margin: 0 0 0.5rem 0;
		color: #f7fafc;
	}
	.lede {
		color: #a0aec0;
		margin: 0;
		max-width: 46rem;
	}
</style>
