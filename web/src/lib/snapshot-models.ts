/**
 * Snapshot definitions by fingerprint, loaded once per page session.
 *
 * Backtests, studies, and bots record the snapshot fingerprint of the rules
 * they used (ADR 0082). "What changed" loads that snapshot and diffs it
 * against the strategy's current definition with the shared semantic-diff
 * helper. Snapshots are content-addressed, so caching by fingerprint is exact.
 */
import { fetchStrategySnapshot, toBuilderModel, type BuilderModel } from './strategies';
import { semanticDiff, type SemanticDiff } from './strategy-diff';

const cache = new Map<string, Promise<BuilderModel>>();

export function loadSnapshotModel(fingerprint: string): Promise<BuilderModel> {
	let pending = cache.get(fingerprint);
	if (pending === undefined) {
		pending = fetchStrategySnapshot(fingerprint).then((snapshot) =>
			toBuilderModel(snapshot.strategy, 0)
		);
		pending.catch(() => cache.delete(fingerprint));
		cache.set(fingerprint, pending);
	}
	return pending;
}

export type SnapshotDiffView =
	{ status: 'ready'; diff: SemanticDiff } | { status: 'unavailable'; reason: string };

/** Diff earlier rules (`fingerprint`) against the current definition. */
export async function diffSnapshotAgainst(
	fingerprint: string,
	current: BuilderModel
): Promise<SnapshotDiffView> {
	try {
		const before = await loadSnapshotModel(fingerprint);
		return { status: 'ready', diff: semanticDiff(before, current) };
	} catch (caught) {
		return {
			status: 'unavailable',
			reason: caught instanceof Error ? caught.message : 'The earlier rules could not be loaded.'
		};
	}
}

/** Test-only: forget cached snapshots. */
export function clearSnapshotCache(): void {
	cache.clear();
}
