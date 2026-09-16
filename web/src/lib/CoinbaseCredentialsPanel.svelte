<script lang="ts">
	import { onMount } from 'svelte';
	import {
		clearCoinbaseCredentials,
		fetchCoinbaseCredentialsStatus,
		setCoinbaseCredentials,
		type CoinbaseCredentialsStatus
	} from '$lib/credentials';

	let status = $state<CoinbaseCredentialsStatus | null>(null);
	let loading = $state(true);
	let submitting = $state(false);
	let error = $state<string | null>(null);
	let notice = $state<string | null>(null);
	let apiKeyName = $state('');
	let privateKey = $state('');
	let confirmed = $state(false);

	async function loadStatus(): Promise<void> {
		loading = true;
		error = null;
		try {
			status = await fetchCoinbaseCredentialsStatus();
		} catch (caught) {
			status = null;
			error =
				caught instanceof Error ? caught.message : 'Could not load Coinbase credential status.';
		} finally {
			loading = false;
		}
	}

	function wipeForm(): void {
		apiKeyName = '';
		privateKey = '';
		confirmed = false;
	}

	async function saveCredentials(): Promise<void> {
		if (!confirmed || submitting) return;
		submitting = true;
		error = null;
		notice = null;
		const keyName = apiKeyName;
		const pem = privateKey;
		wipeForm();
		try {
			status = await setCoinbaseCredentials({ api_key_name: keyName, private_key: pem });
			notice =
				'Coinbase credentials were stored server-side. Restart Compose or native workers before live or ingest uses the new keys. This does not arm live trading.';
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not set Coinbase credentials.';
		} finally {
			submitting = false;
		}
	}

	async function clearCredentials(): Promise<void> {
		if (!confirmed || submitting) return;
		const ok = window.confirm(
			'Clear Coinbase Advanced Trade credentials from this API process and the env file if writable? Workers still need a restart. This does not cancel orders.'
		);
		if (!ok) return;
		submitting = true;
		error = null;
		notice = null;
		wipeForm();
		try {
			status = await clearCoinbaseCredentials();
			notice =
				'Coinbase credentials were cleared. Restart workers so they return to demo. This does not cancel resting orders.';
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not clear Coinbase credentials.';
		} finally {
			submitting = false;
		}
	}

	onMount(() => {
		void loadStatus();
	});
</script>

<section class="card" aria-label="Coinbase credential status">
	<h2>Coinbase credentials</h2>
	<p class="lede">
		Set, rotate, or clear Coinbase Advanced Trade API secrets. Keys stay server-side. This form
		never echoes them, never logs them, and never puts them in a GET body. View + Trade is enough;
		extra permissions are reported, not treated as consent. LLM keys stay on Chat. Extra exchanges
		are out of scope.
	</p>
	{#if loading}
		<p class="muted">Loading status…</p>
	{:else if status}
		<dl>
			<div>
				<dt>Configured in this API process</dt>
				<dd>{status.configured ? 'Yes' : 'No'}</dd>
			</div>
			<div>
				<dt>Env file writable</dt>
				<dd>{status.env_file_writable ? 'Yes' : 'No'}</dd>
			</div>
		</dl>
		<p class="muted">{status.workers_restart_detail}</p>
	{/if}
</section>
<form
	class="card"
	aria-label="Set or rotate Coinbase credentials"
	onsubmit={(event) => {
		event.preventDefault();
		void saveCredentials();
	}}
>
	<h2>Set or rotate</h2>
	<label
		>API key name
		<input
			bind:value={apiKeyName}
			autocomplete="off"
			spellcheck="false"
			placeholder="organizations/…/apiKeys/…"
		/>
	</label>
	<label
		>EC private key (PEM)
		<textarea
			bind:value={privateKey}
			autocomplete="off"
			spellcheck="false"
			rows="8"
			placeholder="-----BEGIN EC PRIVATE KEY-----"></textarea>
	</label>
	<label class="confirm">
		<input type="checkbox" bind:checked={confirmed} />
		I understand these secrets are stored server-side, are never shown again, and that saving them does
		not arm live trading.
	</label>
	<div class="actions">
		<button
			class="refresh"
			type="submit"
			disabled={!confirmed || submitting || apiKeyName.trim() === '' || privateKey.trim() === ''}
		>
			{submitting ? 'Saving…' : 'Save Coinbase credentials'}
		</button>
		<button
			class="danger"
			type="button"
			disabled={!confirmed || submitting}
			onclick={() => void clearCredentials()}
		>
			Clear credentials
		</button>
	</div>
	{#if error}
		<p class="problem" role="alert">{error}</p>
	{/if}
	{#if notice}
		<p class="ok" role="status">{notice}</p>
	{/if}
</form>

<style>
	.card {
		border: 1px solid #303a3c;
		border-radius: 12px;
		background: #141b1c;
		padding: 24px;
		margin-bottom: 20px;
		display: grid;
		gap: 14px;
		max-width: 720px;
	}
	.card h2 {
		margin: 0;
		font-size: 16px;
	}
	.lede {
		margin: 0;
		color: #d8e1e2;
		font-size: 14px;
	}
	dl {
		display: grid;
		gap: 10px;
		margin: 0;
	}
	dt {
		color: #aeb9bb;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	dd {
		margin: 4px 0 0;
		font-size: 14px;
	}
	label {
		display: grid;
		gap: 6px;
		font-size: 12px;
		color: #aeb9bb;
	}
	input,
	textarea {
		border: 1px solid #303a3c;
		border-radius: 8px;
		background: #101617;
		color: #edf3f3;
		padding: 10px 12px;
		font: inherit;
		font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
		font-size: 13px;
	}
	.confirm {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		color: #d8e1e2;
		font-size: 13px;
		text-transform: none;
	}
	.confirm input {
		width: auto;
		margin-top: 3px;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
	}
	.danger {
		color: #f0a3a3;
		background: #151b1d;
		border: 1px solid #5c3232;
		border-radius: 9px;
		padding: 11px 15px;
		cursor: pointer;
	}
	.danger:disabled,
	.refresh:disabled {
		opacity: 0.55;
		cursor: default;
	}
	.muted {
		color: #8d999c;
		font-size: 13px;
		margin: 0;
	}
	.problem {
		color: #f0a3a3;
		margin: 0;
	}
	.ok {
		color: #83d5a3;
		margin: 0;
	}
</style>
