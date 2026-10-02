<script lang="ts">
	/**
	 * In-app operator chat (ADR 0051), shared by the `/chat` page and the shell's
	 * Agent side panel (ADR 0079).
	 *
	 * Safety is identical in both hosts: the LLM key is write-only (never echoed,
	 * field wiped after save), every mutation waits in "Confirmation required",
	 * and live pending actions need the understand-live checkbox before
	 * `i_understand_live` is sent. CSRF comes from `$lib/operator-chat`.
	 */
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

	let {
		variant = 'page',
		loading = $bindable(),
		prefill = null,
		prefillSequence = 0
	}: {
		/** `page` is the full `/chat` view; `panel` is the compact shell side panel. */
		variant?: 'page' | 'panel';
		loading?: boolean;
		/** A drafted question from the page (for example "Ask why"); never auto-sent. */
		prefill?: string | null;
		/** Changes with every new prefill so a repeated question is drafted again. */
		prefillSequence?: number;
	} = $props();

	const uid = $props.id();
	const draftId = `chat-draft-${uid}`;

	let transcript = $state<ChatTranscript | null>(null);
	let sending = $state(false);
	let error = $state<string | null>(null);
	let draft = $state('');
	let apiKey = $state('');
	let provider = $state<LlmProvider>('openai');
	let model = $state('gpt-4o-mini');
	let baseUrl = $state('');
	let understandLive = $state<Record<string, boolean>>({});

	let draftedSequence = -1;
	$effect(() => {
		if (prefill === null || prefillSequence === draftedSequence) return;
		draftedSequence = prefillSequence;
		draft = prefill;
	});

	const visible = $derived(transcript ? visibleChatMessages(transcript.messages) : []);
	const configured = $derived(transcript?.status.llm_configured === true);

	export async function loadTranscript(): Promise<void> {
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

{#snippet keyForm()}
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
			<button type="submit" class="btn primary" data-testid="save-llm-key">Save LLM key</button>
			<button type="button" class="btn" onclick={clearKey} data-testid="clear-llm-key">
				Clear
			</button>
		</div>
	</form>
{/snippet}

<div class="operator-chat" class:panel-variant={variant === 'panel'} aria-busy={loading === true}>
	{#if error}
		<div class="error-banner" role="alert">
			<div>
				<strong>Operator chat error</strong>
				<p>{error}</p>
			</div>
			<button type="button" onclick={loadTranscript}>Try again</button>
		</div>
	{/if}

	{#if variant === 'page'}
		<section class="section" data-testid="llm-key-panel">
			<h2>LLM key (this API process)</h2>
			{@render keyForm()}
		</section>
	{:else}
		<details class="section key-details" data-testid="llm-key-panel" open={!configured}>
			<summary>
				LLM key
				<span class="chip" class:paper={configured}>{configured ? 'Configured' : 'Not set'}</span>
			</summary>
			{@render keyForm()}
		</details>
	{/if}

	{#if transcript && transcript.pending_confirmations.length > 0}
		<section class="section pending" data-testid="pending-confirmations">
			<h2>Confirmation required</h2>
			<p class="hint">
				Mutations use the same HTTP skill contracts as the CLIs. Confirming is the in-app
				<code>--confirm</code>. Live start and live place-order also need understand-live.
			</p>
			{#each transcript.pending_confirmations as pending (pending.id)}
				<article class="confirm" class:live={pending.requires_understand_live}>
					<p>
						{#if pending.requires_understand_live}<span class="chip live">LIVE</span>{/if}
						<code>{pending.lane}</code> · {pending.summary}
					</p>
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
							class="btn"
							onclick={() => void decide(pending.id, false)}
							disabled={sending}
						>
							Reject
						</button>
						<button
							type="button"
							class="btn {pending.requires_understand_live ? 'live' : 'primary'}"
							onclick={() => void decide(pending.id, true)}
							disabled={sending}
							data-testid="confirm-mutation"
						>
							Confirm
						</button>
					</div>
				</article>
			{/each}
		</section>
	{/if}

	<section class="section transcript" data-testid="chat-transcript">
		<div class="transcript-head">
			<h2>Conversation</h2>
			<button type="button" class="btn ghost small" onclick={resetConversation}>Reset</button>
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
			<label class="sr-only" for={draftId}>Message</label>
			<textarea
				id={draftId}
				bind:value={draft}
				rows={variant === 'panel' ? 2 : 3}
				placeholder={configured ? 'Ask or instruct…' : 'Save an LLM key to start'}
				disabled={!configured || sending}
				data-testid="chat-draft"></textarea>
			<button
				type="submit"
				class="btn primary"
				disabled={!configured || sending || !draft.trim()}
				data-testid="send-chat"
			>
				Send
			</button>
		</form>
	</section>
</div>

<style>
	.operator-chat {
		display: grid;
		gap: var(--space-4);
	}
	.section {
		padding: var(--space-4) var(--space-5);
		border: 1px solid var(--line);
		border-radius: var(--radius-lg);
		background: var(--surface);
	}
	.section > h2 {
		margin-bottom: var(--space-2);
	}
	.hint {
		color: var(--muted);
		margin: 0 0 var(--space-3);
		max-width: 46rem;
	}
	.status {
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
		margin: 0 0 var(--space-3);
	}
	.key-form,
	.composer {
		display: grid;
		gap: var(--space-3);
	}
	label {
		display: grid;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	input,
	select,
	textarea {
		min-height: 36px;
		padding: 8px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	textarea {
		resize: vertical;
	}
	.actions {
		display: flex;
		gap: var(--space-2);
	}
	.pending {
		border-color: var(--accent-line);
	}
	.confirm {
		border: 1px solid var(--accent);
		background: var(--accent-soft);
		border-radius: 10px;
		padding: var(--space-3);
		margin-bottom: var(--space-3);
	}
	.confirm.live {
		border-color: var(--live);
		background: var(--live-soft);
	}
	.confirm p {
		margin: 0 0 var(--space-2);
	}
	.confirm p .chip {
		margin-right: 6px;
		vertical-align: 1px;
	}
	.live-ack {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: var(--space-2) 0 var(--space-3);
		color: var(--text);
		font-size: var(--fs-base);
	}
	.live-ack input {
		min-height: 0;
	}
	.transcript-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: var(--space-2);
	}
	.small {
		min-height: 28px;
		padding: 0 8px;
		font-size: var(--fs-sm);
	}
	ol {
		list-style: none;
		padding: 0;
		margin: 0 0 var(--space-4);
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.bubble {
		max-width: 92%;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: 10px;
		background: var(--surface);
	}
	.bubble[data-role='user'] {
		align-self: flex-end;
		background: var(--surface-2);
	}
	.role {
		margin: 0 0 4px;
		font-size: var(--fs-xs);
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--faint);
	}
	.content {
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.composer .btn {
		justify-self: end;
	}

	/* Side-panel density: flatter sections, the transcript fills the column. */
	.panel-variant {
		gap: 0;
		min-height: 100%;
		grid-template-rows: auto auto auto 1fr;
	}
	.panel-variant .section {
		border: 0;
		border-bottom: 1px solid var(--line);
		border-radius: 0;
		background: transparent;
		padding: var(--space-3) 14px;
	}
	.panel-variant .error-banner {
		margin: var(--space-3) 14px;
	}
	.panel-variant .transcript {
		display: flex;
		flex-direction: column;
		border-bottom: 0;
	}
	.panel-variant .transcript ol {
		flex: 1;
	}
	.panel-variant .composer {
		position: sticky;
		bottom: 0;
		padding-top: var(--space-3);
		background: var(--rail);
	}
	.key-details summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		min-height: 28px;
		cursor: pointer;
		font-weight: 600;
		list-style: none;
	}
	.key-details summary::-webkit-details-marker {
		display: none;
	}
	.key-details[open] summary {
		margin-bottom: var(--space-3);
	}
</style>
