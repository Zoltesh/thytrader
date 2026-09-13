<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import favicon from '$lib/assets/favicon.svg';
	import {
		DEFAULT_CONTEXT_LABEL,
		WORKSTATION_NAV,
		isWorkstationNavActive
	} from '$lib/workstation-chrome';
	import type { Snippet } from 'svelte';
	import '../app.css';

	let { children }: { children: Snippet } = $props();

	const contextLabel = $derived(page.data.contextLabel ?? DEFAULT_CONTEXT_LABEL);
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>ThyTrader</title>
	<meta name="description" content="A local-first Coinbase portfolio and strategy workstation." />
</svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home">
			<span class="brand-mark">T</span>
			<span>ThyTrader</span>
		</a>
		<nav aria-label="Primary navigation">
			{#each WORKSTATION_NAV as item (item.href)}
				{@const active = isWorkstationNavActive(item.href, page.route.id)}
				<a href={resolve(item.href)} class:active aria-current={active ? 'page' : undefined}>
					{item.label}
				</a>
			{/each}
		</nav>
		<div class="local-pill">
			<span aria-hidden="true"></span>
			{contextLabel}
		</div>
	</header>
	{@render children()}
</div>
