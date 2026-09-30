/**
 * Shared state for one strategy workspace, provided by
 * `routes/strategies/[id]/+layout.svelte` to the Build / Test / Run / Why
 * stages through Svelte context.
 *
 * It owns the identity history (published versions plus the open draft), the
 * `?version=` resolution, and a cache of published definitions by
 * fingerprint. Stages read it; only the layout reloads it.
 */
import { page } from '$app/state';
import { getContext, setContext } from 'svelte';
import {
	fetchStrategyHistory,
	fetchStrategySource,
	toBuilderModel,
	type BuilderModel,
	type StrategyVersionHistory
} from '$lib/strategies';
import { resolveWorkspaceVersion } from '$lib/strategy-workspace';

const KEY = Symbol('strategy-workspace');

export class StrategyWorkspace {
	strategyId = $state('');
	history = $state<StrategyVersionHistory | null>(null);
	loading = $state(true);
	error = $state<string | null>(null);
	/** Raw `?version=` value from the URL (null when absent). */
	readonly requestedVersion = $derived(page.url.searchParams.get('version'));
	/** Published definitions keyed by exact fingerprint. */
	models = $state<Record<string, BuilderModel>>({});
	modelErrors = $state<Record<string, string>>({});
	/** Set by the Build stage while the draft has unsaved edits. */
	draftDirty = $state(false);
	/** Live draft name while editing, so the identity bar follows the form. */
	draftName = $state<string | null>(null);

	#requestId = 0;

	readonly version = $derived(
		resolveWorkspaceVersion(this.requestedVersion, this.history?.versions ?? [])
	);
	readonly draft = $derived(this.history?.draft ?? null);
	readonly selectedFingerprint = $derived(this.version.entry?.strategy_fingerprint ?? null);
	readonly selectedModel = $derived(
		this.selectedFingerprint === null ? null : (this.models[this.selectedFingerprint] ?? null)
	);
	readonly latestFingerprint = $derived(
		this.history?.versions[this.history.versions.length - 1]?.strategy_fingerprint ?? null
	);
	/** Definition used for name / market / clock: the draft, else the latest published version. */
	readonly identityModel = $derived.by((): BuilderModel | null => {
		const draft = this.history?.draft ?? null;
		if (draft !== null) return toBuilderModel(draft.strategy, draft.revision);
		const latest = this.latestFingerprint;
		return latest === null ? null : (this.models[latest] ?? null);
	});
	readonly name = $derived(this.draftName ?? this.identityModel?.name ?? null);

	async load(strategyId: string): Promise<void> {
		const requestId = ++this.#requestId;
		this.strategyId = strategyId;
		this.loading = true;
		this.error = null;
		try {
			const history = await fetchStrategyHistory(strategyId);
			if (requestId !== this.#requestId) return;
			if (strategyId !== this.strategyId) return;
			this.history = history;
			const latest = history.versions[history.versions.length - 1];
			if (history.draft === null && latest !== undefined) {
				await this.ensureModel(latest.strategy_fingerprint);
			}
		} catch (caught) {
			if (requestId !== this.#requestId) return;
			this.history = null;
			this.error = caught instanceof Error ? caught.message : 'Could not load this strategy.';
		} finally {
			if (requestId === this.#requestId) this.loading = false;
		}
	}

	/** Refresh history without blanking the page (after publish, revise, save). */
	async refresh(): Promise<void> {
		try {
			const history = await fetchStrategyHistory(this.strategyId);
			this.history = history;
		} catch (caught) {
			this.error = caught instanceof Error ? caught.message : 'Could not refresh this strategy.';
		}
	}

	/** Load (once) the immutable published definition for a fingerprint. */
	async ensureModel(fingerprint: string): Promise<BuilderModel | null> {
		const cached = this.models[fingerprint];
		if (cached !== undefined) return cached;
		try {
			const source = await fetchStrategySource(fingerprint);
			const model = toBuilderModel(source, 0);
			this.models = { ...this.models, [fingerprint]: model };
			return model;
		} catch (caught) {
			this.modelErrors = {
				...this.modelErrors,
				[fingerprint]:
					caught instanceof Error ? caught.message : 'Could not load the published definition.'
			};
			return null;
		}
	}

	versionNumberOf(fingerprint: string | null): number | null {
		if (fingerprint === null) return null;
		return (
			this.history?.versions.find((version) => version.strategy_fingerprint === fingerprint)
				?.version ?? null
		);
	}
}

export function provideWorkspace(workspace: StrategyWorkspace): void {
	setContext(KEY, workspace);
}

export function useWorkspace(): StrategyWorkspace {
	const workspace = getContext<StrategyWorkspace | undefined>(KEY);
	if (workspace === undefined) {
		throw new Error('useWorkspace() must run inside the strategy workspace layout.');
	}
	return workspace;
}
