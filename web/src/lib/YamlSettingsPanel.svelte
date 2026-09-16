<script lang="ts">
	import { onMount } from 'svelte';
	import {
		LOG_LEVELS,
		YOLO_TIERS,
		fetchYamlSettings,
		saveYamlSettings,
		toYamlSettingsWrite,
		type NotifyProvider,
		type YamlSettingsView,
		type YamlSettingsWrite,
		type YoloTier
	} from '$lib/settings';

	let view = $state<YamlSettingsView | null>(null);
	let draft = $state<YamlSettingsWrite | null>(null);
	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let saved = $state(false);

	const productPattern = '^[A-Z0-9]{2,20}-USD$';
	const notifyProviders: NotifyProvider[] = ['none', 'log', 'webhook'];

	async function loadSettings(): Promise<void> {
		loading = true;
		error = null;
		saved = false;
		try {
			view = await fetchYamlSettings();
			draft = toYamlSettingsWrite(view);
		} catch (caught) {
			view = null;
			draft = null;
			error = caught instanceof Error ? caught.message : 'YAML settings are unavailable.';
		} finally {
			loading = false;
		}
	}

	function toggleTier(tier: YoloTier, checked: boolean): void {
		if (draft === null) {
			return;
		}
		const next = checked
			? [...draft.yolo_tiers, tier]
			: draft.yolo_tiers.filter((item) => item !== tier);
		draft = { ...draft, yolo_tiers: YOLO_TIERS.filter((item) => next.includes(item)) };
	}

	async function save(): Promise<void> {
		if (draft === null || saving) {
			return;
		}
		saving = true;
		error = null;
		saved = false;
		try {
			view = await saveYamlSettings(draft);
			draft = toYamlSettingsWrite(view);
			saved = true;
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not save YAML settings.';
		} finally {
			saving = false;
		}
	}

	onMount(() => {
		void loadSettings();
	});
</script>

<section class="card" aria-label="YAML settings and YOLO">
	<h2>YAML settings and YOLO</h2>
	<p>
		Non-secret knobs live in <code>thytrader.yaml</code> and apply without restarting API or
		workers. Tiers are an independent subset, not a hierarchy. Live still needs
		<code>--i-understand-live</code>. The playbook never starts live. Secrets stay out of YAML.
	</p>
	{#if loading}
		<p>Loading YAML settings…</p>
	{:else if draft === null || view === null}
		<p>{error ?? 'YAML settings are unavailable.'}</p>
		<button type="button" onclick={loadSettings}>Retry</button>
	{:else}
		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}
		{#if saved}
			<p class="ok" role="status">Applied without restart. Live still needs --i-understand-live.</p>
		{/if}
		<form
			onsubmit={(event) => {
				event.preventDefault();
				void save();
			}}
		>
			<label class="toggle">
				<input type="checkbox" bind:checked={draft.yolo_enabled} />
				YOLO enabled
			</label>
			<fieldset>
				<legend>YOLO tiers</legend>
				{#each YOLO_TIERS as tier (tier)}
					<label>
						<input
							type="checkbox"
							checked={draft.yolo_tiers.includes(tier)}
							onchange={(event) => {
								toggleTier(tier, event.currentTarget.checked);
							}}
						/>
						{tier}
					</label>
				{/each}
			</fieldset>
			<label>
				Log level
				<select bind:value={draft.log_level}>
					{#each LOG_LEVELS as level (level)}
						<option value={level}>{level}</option>
					{/each}
				</select>
			</label>
			<label>
				Snapshot interval (seconds)
				<input type="number" min="60" max="86400" bind:value={draft.snapshot_interval_seconds} />
			</label>
			<label>
				Market-data interval (seconds)
				<input
					type="number"
					min="60"
					max="86400"
					bind:value={draft.market_data_worker_interval_seconds}
				/>
			</label>
			<label>
				Market-data lookback (hours)
				<input
					type="number"
					min="1"
					max="2160"
					bind:value={draft.market_data_worker_lookback_hours}
				/>
			</label>
			<label>
				Default ingest product
				<input
					type="text"
					pattern={productPattern}
					bind:value={draft.market_data_worker_product_id}
				/>
			</label>
			<label>
				Execution interval (seconds)
				<input
					type="number"
					min="5"
					max="3600"
					bind:value={draft.execution_worker_interval_seconds}
				/>
			</label>
			<label>
				Notify provider
				<select bind:value={draft.notify_provider}>
					{#each notifyProviders as provider (provider)}
						<option value={provider}>{provider}</option>
					{/each}
				</select>
			</label>
			<p class="meta">
				File <code>{view.settings_file}</code>
				{#if view.yaml_loaded}(loaded){:else}(defaults until saved){/if}. Webhook URL and Coinbase
				keys stay in ignored <code>.env</code>.
			</p>
			<button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save YAML settings'}</button>
		</form>
		<section class="restart" aria-label="Restart-required process identity">
			<h3>Restart required</h3>
			<p>
				Bind address, port, environment, dataset root, database URL, Coinbase keys, and the notify
				webhook URL stay env-at-boot.
			</p>
			<dl>
				<div>
					<dt>API</dt>
					<dd>{view.process.api_host}:{view.process.api_port}</dd>
				</div>
				<div>
					<dt>Environment</dt>
					<dd>{view.process.environment}</dd>
				</div>
				<div>
					<dt>Dataset root</dt>
					<dd>{view.process.market_data_dataset_root}</dd>
				</div>
				<div>
					<dt>Coinbase credentials</dt>
					<dd>{view.process.coinbase_credentials_configured ? 'configured' : 'not configured'}</dd>
				</div>
				<div>
					<dt>Notify webhook</dt>
					<dd>{view.process.notify_webhook_configured ? 'configured' : 'not configured'}</dd>
				</div>
			</dl>
		</section>
	{/if}
</section>

<style>
	.card {
		border: 1px solid #303a3c;
		border-radius: 12px;
		background: #141b1c;
		padding: 24px;
		margin-bottom: 20px;
		display: grid;
		gap: 12px;
		max-width: 720px;
	}
	.card h2,
	.card h3 {
		margin: 0;
		font-size: 16px;
	}
	.card p,
	.meta,
	.restart p {
		margin: 0;
		color: #8d999c;
		font-size: 14px;
	}
	form {
		display: grid;
		gap: 12px;
	}
	label,
	.toggle {
		display: grid;
		gap: 6px;
		color: #dce4e5;
		font-size: 13px;
	}
	.toggle {
		grid-template-columns: auto 1fr;
		align-items: center;
	}
	fieldset {
		border: 1px solid #303a3c;
		border-radius: 8px;
		display: grid;
		gap: 8px;
		color: #dce4e5;
	}
	fieldset label {
		grid-template-columns: auto 1fr;
		align-items: center;
	}
	input,
	select,
	button {
		font: inherit;
		color: #e9edf1;
		background: #151b1d;
		border: 1px solid #303a3c;
		border-radius: 8px;
		padding: 8px 10px;
	}
	button {
		cursor: pointer;
		width: fit-content;
	}
	button:hover {
		border-color: #5ce1b5;
	}
	.error {
		color: #d5a8a8;
	}
	.ok {
		color: #5ce1b5;
	}
	dl {
		display: grid;
		gap: 8px;
		margin: 12px 0 0;
	}
	dl div {
		display: grid;
		grid-template-columns: 160px 1fr;
		gap: 8px;
		font-size: 13px;
	}
	dt {
		color: #778386;
	}
	dd {
		margin: 0;
		color: #dce4e5;
		font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
	}
</style>
