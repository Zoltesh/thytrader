<script lang="ts">
	import CoinbaseCredentialsPanel from '$lib/CoinbaseCredentialsPanel.svelte';
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
				YAML is the source of truth for non-secret knobs, including YOLO. Coinbase Advanced Trade
				secrets are write-only beside that panel. LLM keys stay on Chat. Extra exchanges are out of
				scope.
			</p>
		</div>
	</section>
<<<<<<< HEAD
	<YamlSettingsPanel />
=======
	<section class="card" aria-label="YAML settings and YOLO">
		<h2>YAML settings and YOLO</h2>
		<p>
			Non-secret YAML configuration and runtime-reloadable YOLO tiers are not implemented here. This
			slot stays visible for that sibling surface. Coinbase secrets stay in the section below.
		</p>
	</section>
>>>>>>> d51e184 (fix(ui): prettier, research panel binding, and unused drawer CSS)
	<CoinbaseCredentialsPanel />
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
