<script lang="ts">
	import { onMount } from 'svelte';
	import {
		clearLlmCredentials,
		decideOperatorChatConfirmation,
		fetchOperatorChatTranscript,
		resetOperatorChat,
		saveLlmCredentials,
		sendOperatorChatMessage,
		visibleChatMessages,
		type ChatTranscript,
		type LlmProvider
	} from '$lib/operator-chat';

	let transcript = $state<ChatTranscript | null>(null);
	let loading = $state(true);
	let sending = $state(false);
	let error = $state<string | null>(null);
	let draft = $state('');
	let apiKey = $state('');
	let provider = $state<LlmProvider>('openai');
	let model = $state('gpt-4o-mini');
	let baseUrl = $state('');
	let understandLive = $state<Record<string, boolean>>({});

	const visible = $derived(transcript ? visibleChatMessages(transcript.messages) : []);

	async function loadTranscript(): Promise<void> {
		loading = true;
		error = null;
		try {
			transcript = await fetchOperatorChatTranscript();
		} catch (caught) {
			transcript = null;
			error = caught instanceof Error ? caught.message : 'Operator chat is unavailable.';
		} finally {
			loading = false;
		}
	}

	async function saveKey(): Promise<void> {
		error = null;
		try {
			await saveLlmCredentials({
				provider,
				model: model.trim() || 'gpt-4o-mini',
				base_url: provider === 'openai_compatible' ? baseUrl.trim() : null,
				api_key: apiKey
			});
			apiKey = '';
			transcript = await fetchOperatorChatTranscript();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not store the LLM key.';
		}
	}

	async function clearKey(): Promise<void> {
		error = null;
		try {
			await clearLlmCredentials();
			transcript = await fetchOperatorChatTranscript();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not clear the LLM key.';
		}
	}

	async function send(): Promise<void> {
		const content = draft.trim();
		if (!content || sending) {
			return;
		}
		sending = true;
		error = null;
		try {
			transcript = await sendOperatorChatMessage(content);
			draft = '';
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not send that message.';
		} finally {
			sending = false;
		}
	}

	async function decide(id: string, confirmed: boolean): Promise<void> {
		sending = true;
		error = null;
		try {
			transcript = await decideOperatorChatConfirmation(id, {
				confirmed,
				i_understand_live: understandLive[id] === true
			});
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not record that confirmation.';
		} finally {
			sending = false;
		}
	}

	async function resetConversation(): Promise<void> {
		error = null;
		try {
			transcript = await resetOperatorChat();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not reset the conversation.';
		}
	}

	onMount(() => {
		void loadTranscript();
	});
</script>

<svelte:head>
	<title>Operator chat · ThyTrader</title>
</svelte:head>

<main>
	<section class="hero">
		<div>
			<p class="eyebrow">In-app operator</p>
			<h1>Operator chat</h1>
			<p class="lede">
				Paste <strong>your LLM API key</strong>. This is not a Coinbase form — Coinbase keys stay on
				the separate secrets surface and never enter the browser. The chat uses the same gated skill
				lanes as <code>ops/</code>: operator is read-only; data, research, runtime, and memory
				mutations stay confirmation-gated. Live still needs the understand-live hard gate.
			</p>
		</div>
		<button class="refresh" type="button" onclick={loadTranscript} disabled={loading}>
			{loading ? 'Refreshing…' : 'Refresh'}
		</button>
	</section>

	{#if error}
		<div class="error-banner" role="alert">
			<div>
				<strong>Operator chat error</strong>
				<p>{error}</p>
			</div>
			<button type="button" onclick={loadTranscript}>Try again</button>
		</div>
	{/if}

	<section class="panel" data-testid="llm-key-panel">
		<h2>LLM key (this API process)</h2>
		<p class="hint">
			The key is stored server-side in the API process only. Status never echoes it. Restarting the
			API clears it. Do not paste a Coinbase key here.
		</p>
		{#if transcript}
			<p class="status" data-testid="llm-configured">
				{transcript.status.llm_configured
					? `Configured · ${transcript.status.provider} · ${transcript.status.model}`
					: 'Not configured'}
			</p>
		{/if}
		<form
			class="key-form"
			onsubmit={(event) => {
				event.preventDefault();
				void saveKey();
			}}
		>
			<label>
				Provider
				<select bind:value={provider} data-testid="llm-provider">
					<option value="openai">OpenAI</option>
					<option value="openai_compatible">OpenAI-compatible</option>
				</select>
			</label>
			<label>
				Model
				<input bind:value={model} autocomplete="off" data-testid="llm-model" />
			</label>
			{#if provider === 'openai_compatible'}
				<label>
					Base URL
					<input
						bind:value={baseUrl}
						placeholder="https://example.invalid/v1"
						autocomplete="off"
						data-testid="llm-base-url"
					/>
				</label>
			{/if}
			<label>
				LLM API key
				<input bind:value={apiKey} type="password" autocomplete="off" data-testid="llm-api-key" />
			</label>
			<div class="actions">
				<button type="submit" data-testid="save-llm-key">Save LLM key</button>
				<button type="button" class="secondary" onclick={clearKey} data-testid="clear-llm-key">
					Clear
				</button>
			</div>
		</form>
	</section>

	{#if transcript && transcript.pending_confirmations.length > 0}
		<section class="panel pending" data-testid="pending-confirmations">
			<h2>Confirmation required</h2>
			<p class="hint">
				Mutations use the same HTTP skill contracts as the CLIs. Confirming is the in-app
				<code>--confirm</code>. Live start and live place-order also need understand-live.
			</p>
			{#each transcript.pending_confirmations as pending (pending.id)}
				<article class="card">
					<p><code>{pending.lane}</code> · {pending.summary}</p>
					{#if pending.requires_understand_live}
						<label class="live-ack">
							<input
								type="checkbox"
								checked={understandLive[pending.id] === true}
								onchange={(event) => {
									const target = event.currentTarget;
									understandLive = { ...understandLive, [pending.id]: target.checked };
								}}
								data-testid="understand-live"
							/>
							I understand this is live Coinbase spot trading
						</label>
					{/if}
					<div class="actions">
						<button
							type="button"
							onclick={() => void decide(pending.id, true)}
							disabled={sending}
							data-testid="confirm-mutation"
						>
							Confirm
						</button>
						<button
							type="button"
							class="secondary"
							onclick={() => void decide(pending.id, false)}
							disabled={sending}
						>
							Reject
						</button>
					</div>
				</article>
			{/each}
		</section>
	{/if}

	<section class="panel transcript" data-testid="chat-transcript">
		<div class="panel-heading">
			<h2>Conversation</h2>
			<button type="button" class="secondary" onclick={resetConversation}>Reset</button>
		</div>
		{#if visible.length === 0}
			<p class="hint">
				Ask for health, gaps, a draft, paper deploy, or a journal. The model cannot skip
				confirmation or live arming.
			</p>
		{:else}
			<ol>
				{#each visible as row (row.id)}
					<li class="bubble" data-role={row.role}>
						<p class="role">{row.role}</p>
						<p class="content">{row.content}</p>
					</li>
				{/each}
			</ol>
		{/if}
		<form
			class="composer"
			onsubmit={(event) => {
				event.preventDefault();
				void send();
			}}
		>
			<label class="sr-only" for="chat-draft">Message</label>
			<textarea
				id="chat-draft"
				bind:value={draft}
				rows="3"
				disabled={!transcript?.status.llm_configured || sending}
				data-testid="chat-draft"></textarea>
			<button
				type="submit"
				disabled={!transcript?.status.llm_configured || sending || !draft.trim()}
				data-testid="send-chat"
			>
				Send
			</button>
		</form>
	</section>
</main>

<style>
	.hero {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 1rem;
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
	.lede,
	.hint {
		color: #a0aec0;
		margin: 0;
		max-width: 46rem;
	}
	.hint {
		margin-bottom: 1rem;
	}
	.refresh,
	button {
		background: #2b6cb0;
		color: #fff;
		border: none;
		padding: 0.5rem 1rem;
		border-radius: 0.375rem;
		cursor: pointer;
		font-weight: 600;
	}
	button.secondary {
		background: transparent;
		border: 1px solid #4a5568;
		color: #e2e8f0;
	}
	button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.error-banner {
		margin-top: 1.5rem;
		padding: 1rem;
		border: 1px solid #c53030;
		border-radius: 0.5rem;
		background: #2d1b1b;
		color: #feb2b2;
		display: flex;
		justify-content: space-between;
		gap: 1rem;
	}
	.panel {
		margin-top: 1.5rem;
		padding: 1rem 1.25rem;
		border: 1px solid #2d3748;
		border-radius: 0.5rem;
	}
	.panel-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.key-form,
	.composer {
		display: grid;
		gap: 0.75rem;
	}
	label {
		display: grid;
		gap: 0.35rem;
		color: #cbd5e0;
		font-size: 0.875rem;
	}
	input,
	select,
	textarea {
		background: #0d1214;
		color: #e9edf1;
		border: 1px solid #2d3748;
		border-radius: 0.375rem;
		padding: 0.5rem 0.75rem;
		font: inherit;
	}
	.actions {
		display: flex;
		gap: 0.75rem;
	}
	.status {
		font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
		margin: 0 0 1rem 0;
	}
	.card {
		border: 1px solid #744210;
		background: #1a1408;
		border-radius: 0.5rem;
		padding: 0.75rem 1rem;
		margin-bottom: 0.75rem;
	}
	.live-ack {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 0.75rem 0;
	}
	ol {
		list-style: none;
		padding: 0;
		margin: 0 0 1rem 0;
		display: grid;
		gap: 0.75rem;
	}
	.bubble {
		padding: 0.75rem 1rem;
		border-radius: 0.5rem;
		background: #12181a;
	}
	.bubble[data-role='user'] {
		background: #10241d;
	}
	.role {
		margin: 0 0 0.35rem 0;
		font-size: 0.75rem;
		text-transform: uppercase;
		color: #a0aec0;
	}
	.content {
		margin: 0;
		white-space: pre-wrap;
	}
	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
	}
</style>
