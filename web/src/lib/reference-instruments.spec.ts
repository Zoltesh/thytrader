import { describe, expect, it } from 'vitest';

import { requiredClocks } from './data-readiness';
import {
	defaultReferenceInstrument,
	fromBuilderModel,
	operandChoices,
	toBuilderModel,
	validReferenceTimeframes,
	type BuilderModel,
	type StrategyDefinition
} from './strategies';
import { plainEnglishSummary, validateDefinition } from './strategy-insight';

/** An alt EMA cross gated by BTC-USDC 1d close > EMA(100) (ADR 0096). */
const definition = {
	schema_version: '1.0',
	strategy_id: '01985cf0-7b60-7000-8000-000000000096',
	name: 'Gated alt',
	description: null,
	created_at: '2026-09-01T00:00:00Z',
	instrument: { product_id: 'SOL-USDC', base_currency: 'SOL', quote_currency: 'USDC' },
	timeframe: '1h',
	data_requirements: {
		warmup_bars: 50,
		required_fields: ['open', 'high', 'low', 'close', 'volume'],
		reference_instruments: [{ id: 'btc', product_id: 'BTC-USDC', timeframe: '1d' }]
	},
	indicators: [
		{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
		{ id: 'slow', kind: 'ema', input: 'close', parameters: { period: 50 } },
		{ id: 'atr', kind: 'atr', input: ['high', 'low', 'close'], parameters: { period: 14 } },
		{ id: 'btc_close', kind: 'identity', input: 'close', source: 'btc', parameters: {} },
		{ id: 'btc_ema', kind: 'ema', input: 'close', source: 'btc', parameters: { period: 100 } }
	],
	entry: {
		side: 'long',
		when: {
			all: [
				{
					left: { indicator: 'fast' },
					operator: 'crosses_above',
					right: { indicator: 'slow' }
				},
				{
					left: { indicator: 'btc_close' },
					operator: 'greater_than',
					right: { indicator: 'btc_ema' }
				}
			]
		},
		cooldown_bars: 0,
		max_open_positions: 1
	},
	sizing: {
		kind: 'risk_fraction',
		risk_fraction: '0.005',
		min_quote_notional: '10',
		max_quote_notional: '100'
	},
	portfolio_limits: { max_strategy_exposure_fraction: '0.10', max_concurrent_positions: 1 },
	exits: {
		initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr', multiple: '2' },
		take_profit: { kind: 'reward_risk', multiple: '2' },
		trailing_stop: { enabled: false },
		time_exit: { max_bars_held: 96 }
	},
	execution: {
		entry_preference: 'maker_only',
		max_entry_wait_bars: 2,
		on_unfilled_entry: 'cancel'
	},
	metadata: { tags: [], notes: [] }
} as StrategyDefinition;

function model(): BuilderModel {
	return toBuilderModel(structuredClone(definition), 1);
}

describe('reference instruments in the builder', () => {
	it('round-trips references and indicator sources without a timeframe', () => {
		const saved = fromBuilderModel(model()) as unknown as {
			data_requirements: { reference_instruments?: unknown };
			indicators: Record<string, unknown>[];
		};
		expect(saved.data_requirements.reference_instruments).toEqual([
			{ id: 'btc', product_id: 'BTC-USDC', timeframe: '1d' }
		]);
		const btcEma = saved.indicators.find((indicator) => indicator.id === 'btc_ema');
		expect(btcEma).toMatchObject({ source: 'btc' });
		expect(btcEma).not.toHaveProperty('timeframe');
		const fast = saved.indicators.find((indicator) => indicator.id === 'fast');
		expect(fast).not.toHaveProperty('source');
	});

	it('omits reference_instruments when none are declared', () => {
		const plain = model();
		plain.reference_instruments = [];
		plain.indicators = plain.indicators.filter((indicator) => !indicator.source);
		const saved = fromBuilderModel(plain) as unknown as {
			data_requirements: Record<string, unknown>;
		};
		expect(saved.data_requirements).not.toHaveProperty('reference_instruments');
	});

	it('labels reference operands with the base currency and reference clock', () => {
		const choices = operandChoices(model().indicators, model().reference_instruments);
		const label = choices.find((choice) => choice.key === 'indicator:btc_ema')?.label;
		expect(label).toMatch(/^BTC · EMA\(100\)/);
		expect(label).toContain('1d');
		const fast = choices.find((choice) => choice.key === 'indicator:fast')?.label;
		expect(fast).not.toContain('BTC');
	});

	it('offers the decision clock and coarser integer multiples as reference clocks', () => {
		const clocks = validReferenceTimeframes('1h');
		expect(clocks[0]).toBe('1h');
		expect(clocks).toContain('1d');
		expect(clocks).not.toContain('15m');
		expect(validReferenceTimeframes('nope')).toEqual([]);
	});

	it('defaults a new reference to BTC in the strategy quote on 1d with a fresh id', () => {
		const draft = model();
		expect(defaultReferenceInstrument(draft)).toEqual({
			id: 'ref2',
			product_id: 'BTC-USDC',
			timeframe: '1d'
		});
		draft.reference_instruments = [];
		expect(defaultReferenceInstrument(draft).id).toBe('btc');
	});

	it('lists each reference series as a required data clock', () => {
		expect(requiredClocks(model())).toContainEqual({
			productId: 'BTC-USDC',
			timeframe: '1d',
			role: 'reference'
		});
	});

	it('mentions the reference gate in the plain-language summary', () => {
		const summary = plainEnglishSummary(model());
		expect(summary).toContain('BTC-USDC 1d');
		expect(summary).toContain('read-only reference');
	});

	it('validates quote currency, clock, readers, and unknown sources', () => {
		expect(validateDefinition(model())).toEqual([]);
		const wrongQuote = model();
		wrongQuote.reference_instruments[0].product_id = 'BTC-USD';
		expect(validateDefinition(wrongQuote).join(' ')).toContain('quote currency USDC');
		const finerClock = model();
		finerClock.reference_instruments[0].timeframe = '15m';
		expect(validateDefinition(finerClock).join(' ')).toContain('coarser integer multiple');
		const unread = model();
		unread.indicators = unread.indicators.map((indicator) => ({ ...indicator, source: '' }));
		expect(validateDefinition(unread).join(' ')).toContain('must be read by at least one');
		const orphan = model();
		orphan.reference_instruments = [];
		expect(validateDefinition(orphan).join(' ')).toContain('unknown reference instrument');
	});
});
