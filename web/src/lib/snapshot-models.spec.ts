import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearSnapshotCache, diffSnapshotAgainst } from './snapshot-models';
import { toBuilderModel, type StrategyDefinition } from './strategies';

const current = {
	schema_version: '1.0',
	strategy_id: 's',
	name: 'Trend',
	description: null,
	created_at: '2026-09-01T00:00:00Z',
	instrument: { product_id: 'BTC-USDC', base_currency: 'BTC', quote_currency: 'USDC' },
	timeframe: '1h',
	data_requirements: {
		warmup_bars: 60,
		required_fields: ['open', 'high', 'low', 'close', 'volume']
	},
	indicators: [
		{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 21 } },
		{ id: 'atr', kind: 'atr', input: ['high', 'low', 'close'], parameters: { period: 14 } }
	],
	entry: {
		side: 'long',
		when: { all: [] },
		cooldown_bars: 3,
		max_open_positions: 1
	},
	sizing: {
		kind: 'risk_fraction',
		risk_fraction: '0.01',
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

const earlier = {
	...current,
	indicators: [
		{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 12 } },
		(current.indicators as unknown[])[1]
	]
} as StrategyDefinition;

afterEach(() => {
	clearSnapshotCache();
	vi.unstubAllGlobals();
});

describe('diffSnapshotAgainst', () => {
	it('loads the snapshot once and diffs it against the current rules', async () => {
		const fetchMock = vi.fn(async () => ({
			ok: true,
			json: async () => ({
				strategy_fingerprint: 'sha256:x',
				strategy_id: 's',
				strategy_name: 'Trend',
				strategy: earlier,
				created_at: '2026-09-01T00:00:00Z',
				is_current: false
			})
		}));
		vi.stubGlobal('fetch', fetchMock);
		const model = toBuilderModel(current, 1);
		const first = await diffSnapshotAgainst('sha256:x', model);
		const second = await diffSnapshotAgainst('sha256:x', model);
		expect(first.status).toBe('ready');
		if (first.status !== 'ready') throw new Error('unreachable');
		expect(first.diff.changes.length).toBeGreaterThan(0);
		expect(JSON.stringify(first.diff.changes)).toContain('12');
		expect(second.status).toBe('ready');
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it('reports an unavailable snapshot instead of inventing a diff', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => ({
				ok: false,
				status: 404,
				json: async () => ({ detail: { code: 'strategy_snapshot_not_found', message: 'gone' } })
			}))
		);
		const view = await diffSnapshotAgainst('sha256:y', toBuilderModel(current, 1));
		expect(view.status).toBe('unavailable');
	});
});
