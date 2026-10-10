import { describe, expect, it } from 'vitest';
import { definition } from '../e2e/workspace-fixtures';
import { fromBuilderModel, toBuilderModel, type StrategyDefinition } from './strategies';
import {
	defaultDerivatives,
	instrumentKindOf,
	serializeDerivatives,
	toDerivativesDraft,
	validateDerivatives
} from './strategies-derivatives';
import { validateDefinition } from './strategy-insight';
import { semanticDiff } from './strategy-diff';

/** A saved futures strategy as the server returns it (canonical, every futures key set). */
const futuresDefinition: StrategyDefinition = {
	...definition,
	name: 'ETH perp short',
	instrument: {
		product_id: 'ETP-20DEC30-CDE',
		base_currency: 'ETH',
		quote_currency: 'USD',
		kind: 'future'
	},
	entry: { ...definition.entry, side: 'short' },
	derivatives: { max_leverage: '3', margin_mode: 'overnight', flatten_before_expiry_hours: 24 }
};

describe('futures fields in the strategy builder', () => {
	it('round-trips every futures field through the form model', () => {
		const model = toBuilderModel(structuredClone(futuresDefinition), 4);
		expect(model.instrument_kind).toBe('future');
		expect(model.derivatives).toEqual({
			max_leverage: '3',
			margin_mode: 'overnight',
			flatten_before_expiry_hours: 24
		});
		expect(fromBuilderModel(model)).toEqual(futuresDefinition);
	});

	it('keeps the futures fields through an unrelated edit', () => {
		const model = toBuilderModel(structuredClone(futuresDefinition), 4);
		model.name = 'Renamed perp short';
		const saved = fromBuilderModel(model);
		expect(saved.instrument).toEqual(futuresDefinition.instrument);
		expect(saved.derivatives).toEqual(futuresDefinition.derivatives);
		expect(saved.name).toBe('Renamed perp short');
	});

	it('omits flatten_before_expiry_hours when the document has none', () => {
		const perp = {
			...futuresDefinition,
			derivatives: { max_leverage: '2', margin_mode: 'overnight' }
		};
		const saved = fromBuilderModel(toBuilderModel(structuredClone(perp), 1));
		expect(saved.derivatives).toEqual({ max_leverage: '2', margin_mode: 'overnight' });
		expect(saved).toEqual(perp);
	});

	it('leaves a spot document byte-identical: no kind and no derivatives keys', () => {
		const model = toBuilderModel(structuredClone(definition), 1);
		expect(model.instrument_kind).toBe('spot');
		expect(model.derivatives).toBeNull();
		const saved = fromBuilderModel(model);
		expect(JSON.stringify(saved)).toBe(
			JSON.stringify(fromBuilderModel(toBuilderModel(definition, 1)))
		);
		expect(saved).toEqual(definition);
		expect(saved.instrument).not.toHaveProperty('kind');
		expect(saved).not.toHaveProperty('derivatives');
	});

	it('writes a futures instrument settled in USD with the typed underlying', () => {
		const model = toBuilderModel(structuredClone(definition), 1);
		model.instrument_kind = 'future';
		model.product_id = 'BIP-20DEC30-CDE';
		model.base_currency = 'BTC';
		model.derivatives = { ...defaultDerivatives(), max_leverage: '5' };
		const saved = fromBuilderModel(model);
		expect(saved.instrument).toEqual({
			product_id: 'BIP-20DEC30-CDE',
			base_currency: 'BTC',
			quote_currency: 'USD',
			kind: 'future'
		});
		expect(saved.derivatives).toEqual({ max_leverage: '5', margin_mode: 'overnight' });
	});

	it('reads the instrument kind only from instrument.kind', () => {
		expect(instrumentKindOf({ kind: 'future' })).toBe('future');
		expect(instrumentKindOf({ product_id: 'BTC-USD' })).toBe('spot');
		expect(instrumentKindOf(undefined)).toBe('spot');
		expect(toDerivativesDraft(undefined)).toBeNull();
		expect(serializeDerivatives({ ...defaultDerivatives(), max_leverage: ' 2 ' })).toEqual({
			max_leverage: '2',
			margin_mode: 'overnight'
		});
	});

	it('validates the contract id and the leverage bounds', () => {
		expect(validateDerivatives('spot', null, false)).toEqual([]);
		expect(validateDerivatives('future', defaultDerivatives(), true)).toEqual([]);
		expect(validateDerivatives('future', defaultDerivatives(), false)).toEqual([
			'A futures strategy needs a CFM contract id such as ETP-20DEC30-CDE.'
		]);
		for (const leverage of ['0.5', '21', 'two', '']) {
			expect(
				validateDerivatives('future', { ...defaultDerivatives(), max_leverage: leverage }, true)
			).toEqual(['Maximum leverage must be a decimal between 1 and 20.']);
		}
		const model = toBuilderModel(structuredClone(futuresDefinition), 1);
		expect(validateDefinition(model)).toEqual([]);
		model.derivatives = { ...defaultDerivatives(), max_leverage: '25' };
		expect(validateDefinition(model)).toContain(
			'Maximum leverage must be a decimal between 1 and 20.'
		);
	});

	it('names futures changes in the semantic diff and labels notional in USD', () => {
		const before = toBuilderModel(structuredClone(futuresDefinition), 1);
		const after = toBuilderModel(structuredClone(futuresDefinition), 2);
		after.derivatives = { ...defaultDerivatives(), max_leverage: '4' };
		after.sizing.max_quote_notional = '250';
		const diff = semanticDiff(before, after);
		expect(diff.changes.map((change) => change.label)).toEqual(
			expect.arrayContaining([
				'Maximum leverage',
				'Flatten before expiry (hours)',
				'Maximum USD notional'
			])
		);
		const spot = toBuilderModel(structuredClone(definition), 1);
		expect(semanticDiff(spot, toBuilderModel(structuredClone(definition), 2)).changes).toEqual([]);
	});
});
