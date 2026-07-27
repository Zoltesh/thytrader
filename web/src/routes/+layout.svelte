<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>ThyTrader Portfolio</title>
	<meta name="description" content="A local-first Coinbase portfolio and strategy workstation." />
</svelte:head>

{@render children()}
