import { describe, expect, it } from 'vitest';
import { definition } from '../e2e/workspace-fixtures';
import { fromBuilderModel, toBuilderModel } from './strategies';

describe('economic guard builder round trip', () => {
	it('preserves a snapshotted guard during unrelated edits', () => {
		const guard = { minimum_net_target_return_fraction: '0.002' };
		const guarded = { ...definition, entry: { ...definition.entry, economic_guard: guard } };
		const model = toBuilderModel(guarded, 1);
		model.name = 'Renamed guarded strategy';
		expect(fromBuilderModel(model).entry).toEqual({ ...definition.entry, economic_guard: guard });
	});
	it('omits the disabled field from preexisting definitions', () => {
		const model = toBuilderModel(definition, 1);
		expect(fromBuilderModel(model).entry).not.toHaveProperty('economic_guard');
	});
});
