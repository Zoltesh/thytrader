import { describe, expect, it } from 'vitest';
import { exitReasonLines, formatExitReason, type BacktestDiagnostics } from './backtests';
import { exitReasonLabel } from './decisions';
import {
	defaultSignalExit,
	fromBuilderModel,
	toBuilderModel,
	type BuilderModel,
	type ConditionDraft,
	type StrategyDefinition
} from './strategies';
import { summarizeStrategySource } from './strategy-config';
import { semanticDiff } from './strategy-diff';
import { plainEnglishSummary, signalExitSentence, validateDefinition } from './strategy-insight';

/** EMA trend hold (ADR 0093): enter on the cross up, exit on the cross down. */
const definition = {
	schema_version: '1.0',
	strategy_id: '01985cf0-7b60-7000-8000-000000000092',
	name: 'Trend hold',
	description: null,
	created_at: '2026-10-02T00:00:00Z',
	instrument: { product_id: 'BTC-USDC', base_currency: 'BTC', quote_currency: 'USDC' },
	timeframe: '1d',
	data_requirements: {
		warmup_bars: 100,
		required_fields: ['open', 'high', 'low', 'close', 'volume']
	},
	indicators: [
		{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
		{ id: 'slow', kind: 'ema', input: 'close', parameters: { period: 100 } },
		{ id: 'atr', kind: 'atr', input: ['high', 'low', 'close'], parameters: { period: 14 } }
	],
	entry: {
		side: 'long',
		when: {
			all: [
				{
					left: { indicator: 'fast' },
					operator: 'crosses_above',
					right: { indicator: 'slow' }
				}
			]
		},
		cooldown_bars: 3,
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
		initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr', multiple: '3' },
		take_profit: { kind: 'none' },
		trailing_stop: { enabled: true, kind: 'atr_multiple', atr_indicator: 'atr', multiple: '5' },
		time_exit: { max_bars_held: 1000 }
	},
	execution: {
		entry_preference: 'maker_only',
		max_entry_wait_bars: 2,
		on_unfilled_entry: 'cancel'
	},
	metadata: { tags: [], notes: [] }
} as unknown as StrategyDefinition;

function model(): BuilderModel {
	return toBuilderModel(structuredClone(definition), 1);
}

describe('defaultSignalExit', () => {
	it('mirrors a single-cross entry so the trend is held until the reverse cross', () => {
		const built = model();
		expect(defaultSignalExit(built.entry.when, built.indicators)).toEqual({
			when: {
				all: [
					{
						left: { indicator: 'fast' },
						operator: 'crosses_below',
						right: { indicator: 'slow' }
					}
				]
			}
		});
	});

	it('falls back to one editable comparison for other entry trees', () => {
		const built = model();
		const entry: ConditionDraft = {
			all: [
				{ left: { indicator: 'fast' }, operator: 'greater_than', right: { literal: '1' } },
				{ left: { indicator: 'slow' }, operator: 'greater_than', right: { literal: '1' } }
			]
		};
		const fallback = defaultSignalExit(entry, built.indicators);
		expect(fallback.when).toEqual({
			all: [{ left: { indicator: 'fast' }, operator: 'less_than', right: { literal: '0' } }]
		});
	});
});

describe('signal exit in the builder model', () => {
	it('round-trips the rule and omits it from the document when it is absent', () => {
		const built = model();
		expect(built.exits.signal_exit).toBeUndefined();
		expect('signal_exit' in (fromBuilderModel(built).exits as object)).toBe(false);
		built.exits.signal_exit = defaultSignalExit(built.entry.when, built.indicators);
		const document = fromBuilderModel(built);
		const reloaded = toBuilderModel(document, 2);
		expect(reloaded.exits.signal_exit).toEqual(built.exits.signal_exit);
		expect(JSON.parse(JSON.stringify(document)).exits.signal_exit).toEqual(built.exits.signal_exit);
	});

	it('describes the rule in plain language with the protective stop caveat', () => {
		const built = model();
		expect(signalExitSentence(built)).toEqual([]);
		built.exits.signal_exit = defaultSignalExit(built.entry.when, built.indicators);
		const summary = plainEnglishSummary(built);
		expect(summary).toContain('Exit when fast crosses below slow');
		expect(summary).toContain('the initial stop still guards the position');
		const source = summarizeStrategySource('sha256:' + 'a'.repeat(64), fromBuilderModel(built));
		expect(source.exits).toContain('Signal exit: when fast crosses below slow');
		expect(source.exits).toContain('Trailing stop: 5× atr');
	});

	it('validates exit operands with the entry rules', () => {
		const built = model();
		built.exits.signal_exit = {
			when: {
				all: [
					{
						left: { indicator: 'fast' },
						operator: 'crosses_below',
						right: { literal: '1' }
					}
				]
			}
		};
		expect(validateDefinition(built)).toContain('Crossover rules must compare two indicators.');
		built.exits.signal_exit = { when: { all: [] } };
		expect(validateDefinition(built)).toContain('Empty condition groups are not allowed.');
		built.exits.signal_exit = defaultSignalExit(built.entry.when, built.indicators);
		expect(validateDefinition(built)).toEqual([]);
	});

	it('reports adding or changing the rule as a semantic change', () => {
		const before = model();
		const after = model();
		after.exits.signal_exit = defaultSignalExit(after.entry.when, after.indicators);
		const changes = semanticDiff(before, after).changes;
		expect(changes).toContainEqual(
			expect.objectContaining({ label: 'Exit when (signal exit)', from: 'off' })
		);
	});
});

describe('signal exit labels', () => {
	it('names the exit reason in trades, diagnostics, and the decision timeline', () => {
		expect(formatExitReason('signal')).toBe('signal exit');
		expect(formatExitReason('stop_loss')).toBe('stop loss');
		expect(exitReasonLabel('signal')).toBe('signal exit');
		const diagnostics = {
			diagnostics_version: 'thytrader-backtest-diagnostics-v1',
			signals_matched: 3,
			entries_rested: 3,
			entries_filled: 3,
			entries_expired: 0,
			entries_repriced: 0,
			entries_refused_at_fill: 0,
			entries_unfilled_at_end: 0,
			entries_size_capped: 0,
			warmup_bars: 0,
			skipped: [],
			exit_reasons: [
				{ reason: 'signal', count: 2 },
				{ reason: 'stop_loss', count: 1 }
			]
		} satisfies BacktestDiagnostics;
		expect(exitReasonLines(diagnostics)).toEqual(['2 signal exit', '1 stop loss']);
		expect(exitReasonLines({ ...diagnostics, exit_reasons: null })).toEqual([]);
	});
});
