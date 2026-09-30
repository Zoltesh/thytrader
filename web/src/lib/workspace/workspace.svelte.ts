/**
 * Shared state for one strategy workspace, provided by
 * `routes/strategies/[id]/+layout.svelte` to the Build / Test / Run / Why
 * stages through Svelte context.
 *
 * It owns the one mutable strategy record (ADR 0082) and a cache of snapshot
 * definitions by fingerprint, used for "What changed" diffs. Stages read it;
 * Build replaces the record after a save.
 */
import { getContext, setContext } from 'svelte';
import {
	builderModelFromRecord,
	fetchStrategy,
	fetchStrategySnapshot,
	toBuilderModel,
	type BuilderModel,
	type StrategyRecord
} from '$lib/strategies';

const KEY = Symbol('strategy-workspace');

export class StrategyWorkspace {
	strategyId = $state('');
	record = $state<StrategyRecord | null>(null);
	loading = $state(true);
	error = $state<string | null>(null);
	/** Snapshot definitions keyed by exact fingerprint. */
	snapshots = $state<Record<string, BuilderModel>>({});
	snapshotErrors = $state<Record<string, string>>({});
	/** Set by the Build stage while the form has unsaved edits. */
	dirty = $state(false);
	/** Live name while editing, so the identity bar follows the form. */
	draftName = $state<string | null>(null);

	#requestId = 0;

	/** Builder model of the saved document (null when it cannot be shown in the form). */
	readonly model = $derived(this.record === null ? null : builderModelFromRecord(this.record));
	/** Model of the current valid definition; null while the saved document is invalid. */
	readonly validModel = $derived(
		this.record?.strategy ? toBuilderModel(this.record.strategy, this.record.revision) : null
	);
	readonly valid = $derived(this.record?.validation.valid === true);
	readonly issues = $derived(this.record?.validation.issues ?? []);
	readonly currentFingerprint = $derived(this.record?.current_fingerprint ?? null);
	readonly name = $derived(this.draftName ?? this.record?.name ?? null);

	async load(strategyId: string): Promise<void> {
		const requestId = ++this.#requestId;
		this.strategyId = strategyId;
		this.loading = true;
		this.error = null;
		try {
			const record = await fetchStrategy(strategyId);
			if (requestId !== this.#requestId || strategyId !== this.strategyId) return;
			this.record = record;
		} catch (caught) {
			if (requestId !== this.#requestId) return;
			this.record = null;
			this.error = caught instanceof Error ? caught.message : 'Could not load this strategy.';
		} finally {
			if (requestId === this.#requestId) this.loading = false;
		}
	}

	/** Refresh the record without blanking the page. */
	async refresh(): Promise<void> {
		try {
			this.record = await fetchStrategy(this.strategyId);
		} catch (caught) {
			this.error = caught instanceof Error ? caught.message : 'Could not refresh this strategy.';
		}
	}

	/** Replace the record with a save response. */
	accept(record: StrategyRecord): void {
		this.record = record;
	}

	/** Load (once) the snapshot definition a run or bot used. */
	async ensureSnapshot(fingerprint: string): Promise<BuilderModel | null> {
		const cached = this.snapshots[fingerprint];
		if (cached !== undefined) return cached;
		try {
			const snapshot = await fetchStrategySnapshot(fingerprint);
			const model = toBuilderModel(snapshot.strategy, 0);
			this.snapshots = { ...this.snapshots, [fingerprint]: model };
			return model;
		} catch (caught) {
			this.snapshotErrors = {
				...this.snapshotErrors,
				[fingerprint]:
					caught instanceof Error ? caught.message : 'Could not load the earlier rules.'
			};
			return null;
		}
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
