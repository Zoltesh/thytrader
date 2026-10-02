import { describe, expect, it } from 'vitest';
import type { CoinbaseCredentialsStatus } from '$lib/credentials';
import type { StrategyIdentity } from '$lib/deployment-portfolio';
import type { Deployment, DeploymentPosition } from '$lib/deployments';
import type { StrategyLibraryEntry } from '$lib/strategies';
import type { RiskPolicySnapshot } from '$lib/strategy-workspace';
import {
	attentionBotCount,
	backfillCandidates,
	datasetAttention,
	datasetProblemIndex,
	deploymentAttention,
	researchAttention,
	setupAttention,
	sortAttention,
	stuckBackfillReason,
	type AttentionItem
} from './home-attention';
import type { DatasetCoverageRow, IngestionState, ResearchJob } from './home-data';

const NOW = Date.parse('2026-10-02T12:00:00Z');
const fp = (char: string): string => `sha256:${char.repeat(64)}`;

function deployment(overrides: Partial<Deployment> = {}): Deployment {
	return {
		id: 'dep-1',
		strategy_fingerprint: fp('a'),
		strategy_id: 'strategy-1',
		strategy_name: 'EMA Trend Pullback',
		kind: 'strategy',
		timeframe: '1h',
		product_id: 'BTC-USDC',
		mode: 'paper',
		status: 'running',
		phase: 'flat',
		cash: '1000',
		paper_starting_cash: '1000',
		last_evaluated_bar: null,
		last_signal: null,
		mismatch_detail: null,
		pending_entry_bars: 0,
		bars_held: 0,
		lifecycle_command: 'none',
		daily_loss_latched: false,
		drawdown_latched: false,
		revision: 1,
		worker_lease_held: true,
		created_at: '2026-09-24T00:00:00Z',
		updated_at: '2026-09-29T00:00:00Z',
		position: null,
		positions: [],
		orders: [],
		fills: [],
		...overrides
	};
}

function position(protection: string): DeploymentPosition {
	return {
		product_id: 'ETH-USDC',
		quantity: '0.0381',
		entry_price: '2611.40',
		stop_price: '2560.00',
		target_price: '2648.10',
		entered_bar: '2026-10-01T19:00:00Z',
		side: 'long',
		protection_status: protection
	};
}

const names = new Map<string, StrategyIdentity>([
	['strategy-1', { name: 'EMA Trend Pullback', currentFingerprint: fp('a') }],
	['strategy-2', { name: 'RSI Reversion', currentFingerprint: fp('b') }]
]);

function ids(items: readonly AttentionItem[]): string[] {
	return items.map((item) => item.id);
}

describe('deploymentAttention', () => {
	it('flags a paused bot with its mismatch detail and a Review link to the bot', () => {
		const [item, ...rest] = deploymentAttention(
			[
				deployment({
					id: 'uni',
					strategy_id: null,
					strategy_name: 'UNI Momentum',
					product_id: 'UNI-USDC',
					timeframe: '15m',
					status: 'paused',
					mismatch_detail: 'User-order feed was stale for 2m.'
				})
			],
			names
		);
		expect(rest).toEqual([]);
		expect(item).toMatchObject({
			id: 'bot-paused:uni',
			icon: 'pause',
			severity: 'warning',
			label: 'UNI Momentum paused',
			detail: 'Paper · UNI / USDC · 15m · User-order feed was stale for 2m.',
			action: { label: 'Review', href: '/deployments/uni' },
			deploymentId: 'uni',
			live: false
		});
	});

	it('explains a paused bot without a mismatch', () => {
		const [item] = deploymentAttention([deployment({ status: 'paused' })], names);
		expect(item.detail).toBe('Paper · BTC / USDC · 1h · no new entries until it is resumed');
	});

	it('flags a running bot that reports a mismatch; live mismatches are critical', () => {
		const items = deploymentAttention(
			[
				deployment({
					id: 'p',
					mismatch_detail: 'Entry fill is missing stored stop/target prices.'
				}),
				deployment({
					id: 'l',
					mode: 'live',
					strategy_id: 'strategy-2',
					product_id: 'ETH-USDC',
					mismatch_detail: 'Order submit is unconfirmed and has no venue id.'
				})
			],
			names
		);
		expect(items.map((item) => [item.id, item.severity, item.label])).toEqual([
			['bot-mismatch:p', 'warning', 'EMA Trend Pullback reports a mismatch'],
			['bot-mismatch:l', 'critical', 'RSI Reversion reports a mismatch']
		]);
		expect(items[1].detail).toBe(
			'Live · ETH / USDC · 1h · Order submit is unconfirmed and has no venue id.'
		);
	});

	it('lists both tripped breaker latches in one item', () => {
		const [item] = deploymentAttention(
			[deployment({ daily_loss_latched: true, drawdown_latched: true })],
			names
		);
		expect(item).toMatchObject({
			id: 'bot-breaker:dep-1',
			icon: 'breaker',
			label: 'EMA Trend Pullback: daily-loss and drawdown breaker tripped'
		});
		expect(item.detail).toContain('risk-increasing entries stay blocked until the latch is reset');
	});

	it('flags live books without exit cover, and unconfirmed cover separately', () => {
		const items = deploymentAttention(
			[
				deployment({ id: 'bare', mode: 'live', positions: [position('unprotected')] }),
				deployment({ id: 'unknown', mode: 'live', positions: [position('unknown')] }),
				deployment({ id: 'covered', mode: 'live', positions: [position('covered')] }),
				deployment({ id: 'paper', mode: 'paper', positions: [position('unprotected')] })
			],
			names
		);
		expect(items.map((item) => [item.id, item.severity])).toEqual([
			['bot-unprotected:bare', 'critical'],
			['bot-unconfirmed:unknown', 'warning']
		]);
		expect(items[0].label).toBe('EMA Trend Pullback has a live position without exit cover');
	});

	it('skips stopped bots unless a live one still reports a mismatch or holds an unprotected book', () => {
		const items = deploymentAttention(
			[
				deployment({ id: 'paper-stopped', status: 'stopped', mismatch_detail: 'old fault' }),
				deployment({ id: 'latched-stopped', status: 'stopped', daily_loss_latched: true }),
				deployment({
					id: 'live-stopped',
					mode: 'live',
					status: 'stopped',
					mismatch_detail: 'Venue fill could not be reconciled.',
					positions: [position('unprotected')]
				})
			],
			names
		);
		expect(ids(items)).toEqual(['bot-mismatch:live-stopped', 'bot-unprotected:live-stopped']);
		expect(items[0].label).toBe('EMA Trend Pullback stopped with an unresolved mismatch');
	});

	it('flags a status outside running, paused, and stopped', () => {
		const [item] = deploymentAttention([deployment({ status: 'reconciling' })], names);
		expect(item).toMatchObject({
			id: 'bot-status:dep-1',
			label: 'EMA Trend Pullback is reconciling'
		});
	});

	it('raises nothing for a healthy running bot', () => {
		expect(deploymentAttention([deployment()], names)).toEqual([]);
	});
});

describe('setupAttention', () => {
	const missing: CoinbaseCredentialsStatus = {
		provider: 'coinbase',
		configured: false,
		persisted: false,
		env_file_writable: true,
		api_hot_reloaded: false,
		workers_require_restart: false,
		workers_restart_detail: ''
	};
	const compiled: RiskPolicySnapshot = {
		source: 'compiled_default',
		version: 1,
		quote_currency: 'USDC',
		allocations: []
	};

	it('stays quiet without running or paused live bots', () => {
		expect(
			setupAttention({
				deployments: [deployment(), deployment({ mode: 'live', status: 'stopped' })],
				credentials: missing,
				riskPolicy: compiled
			})
		).toEqual([]);
	});

	it('flags missing credentials and an unpublished policy while live bots exist', () => {
		const items = setupAttention({
			deployments: [deployment({ mode: 'live', status: 'paused' })],
			credentials: missing,
			riskPolicy: compiled
		});
		expect(items.map((item) => [item.id, item.severity, item.action])).toEqual([
			['setup:credentials', 'critical', { label: 'Add credentials', href: '/settings' }],
			['setup:risk-policy', 'critical', { label: 'Ask the agent', href: '/chat' }]
		]);
		expect(items[0].detail).toBe(
			'1 live bot is running or paused while no Coinbase credentials are configured.'
		);
		expect(items[1].detail).toContain('thytrader-runtime set-risk-policy --confirm');
	});

	it('never guesses when a source could not be read', () => {
		expect(
			setupAttention({
				deployments: [deployment({ mode: 'live' })],
				credentials: null,
				riskPolicy: null
			})
		).toEqual([]);
	});
});

function row(overrides: Partial<DatasetCoverageRow> = {}): DatasetCoverageRow {
	return {
		provider: 'coinbase',
		product_id: 'SOL-USDC',
		timeframe: '5m',
		watched: true,
		lookback_hours: 2160,
		worker_status: 'succeeded',
		failure_code: null,
		failure_message: null,
		watch_complete: true,
		complete: true,
		freshness_status: 'fresh',
		covered_starts_at: '2026-07-04T12:00:00Z',
		covered_ends_at: '2026-10-02T11:55:00Z',
		expected_candle_count: 25920,
		received_candle_count: 25920,
		gap_count: 0,
		missing_intervals: 0,
		watch_expected_candle_count: 25920,
		watch_status: 'complete',
		history_floor_at: null,
		...overrides
	};
}

function strategy(overrides: Partial<StrategyLibraryEntry> = {}): StrategyLibraryEntry {
	return {
		strategy_id: 'strategy-1',
		name: 'SOL Breakout',
		product_id: 'SOL-USDC',
		timeframe: '5m',
		revision: 3,
		valid: true,
		current_fingerprint: fp('c'),
		summary: null,
		created_at: '2026-09-01T00:00:00Z',
		updated_at: '2026-09-30T00:00:00Z',
		backtest: null,
		paper_live: { paper: 'not_deployed', live: 'not_deployed' },
		active_deployment_count: 0,
		...overrides
	};
}

function ingestion(overrides: Partial<IngestionState> = {}): IngestionState {
	return {
		product_id: 'SOL-USDC',
		timeframe: '5m',
		status: 'succeeded',
		last_attempt_at: '2026-10-02T11:55:00Z',
		last_success_at: '2026-10-02T11:55:00Z',
		next_attempt_at: '2026-10-02T12:00:00Z',
		watch_complete: false,
		failure: null,
		...overrides
	};
}

describe('datasetAttention', () => {
	it('ignores unwatched and healthy datasets', () => {
		expect(
			datasetAttention({
				rows: [row(), row({ watched: false, freshness_status: 'stale' })],
				ingestion: {},
				strategies: [],
				nowMs: NOW
			})
		).toEqual([]);
	});

	it('reports a failed backfill with the redacted failure message', () => {
		const [item] = datasetAttention({
			rows: [
				row({
					worker_status: 'failed',
					watch_status: 'backfilling',
					failure_code: 'provider_unavailable',
					failure_message: 'Historical market-data retrieval failed.'
				})
			],
			ingestion: {},
			strategies: [],
			nowMs: NOW
		});
		expect(item).toMatchObject({
			id: 'data:SOL-USDC:5m',
			icon: 'data',
			label: 'SOL-USDC 5m backfill failed',
			detail: 'Historical market-data retrieval failed.',
			action: { label: 'Data health', fragment: 'data-health' }
		});
	});

	it('names missing bars, folds secondary problems into the detail, and links the strategy', () => {
		const [item] = datasetAttention({
			rows: [row({ gap_count: 1, missing_intervals: 3, freshness_status: 'stale' })],
			ingestion: {},
			strategies: [
				strategy({ strategy_id: 'older', name: 'SOL Fade', updated_at: '2026-09-01T00:00:00Z' }),
				strategy({ strategy_id: 'newer', name: 'SOL Breakout' }),
				strategy({ strategy_id: 'other', product_id: 'SOL-USD' })
			],
			nowMs: NOW
		});
		expect(item.label).toBe('SOL-USDC 5m data is stale');
		expect(item.detail).toBe(
			'Newest verified candle Oct 2 11:55 UTC · also 3 missing bars · used by SOL Breakout and 1 more'
		);
		expect(item.action).toEqual({ label: 'Open strategy', href: '/strategies/newer/test' });
	});

	it('describes gaps in the verified range', () => {
		const [item] = datasetAttention({
			rows: [row({ gap_count: 2, missing_intervals: 0 })],
			ingestion: {},
			strategies: [],
			nowMs: NOW
		});
		expect(item).toMatchObject({ icon: 'gap', label: 'SOL-USDC 5m has 2 gaps' });
		expect(item.detail).toContain(
			'2 gaps in the verified range between Jul 4 12:00 UTC and Oct 2 11:55 UTC'
		);
	});

	it('reports a stuck backfill from its overdue ingestion schedule with progress', () => {
		const backfilling = row({
			watch_status: 'backfilling',
			received_candle_count: 4000,
			watch_expected_candle_count: 25920
		});
		expect(
			backfillCandidates([backfilling, row(), row({ watched: false, watch_status: 'backfilling' })])
		).toEqual([backfilling]);
		const [item] = datasetAttention({
			rows: [backfilling],
			ingestion: {
				'SOL-USDC|5m': ingestion({
					last_attempt_at: '2026-10-02T10:00:00Z',
					next_attempt_at: '2026-10-02T10:05:00Z'
				})
			},
			strategies: [],
			nowMs: NOW
		});
		expect(item).toMatchObject({ icon: 'clock', label: 'SOL-USDC 5m backfill is stuck' });
		expect(item.detail).toBe(
			'No attempt since Oct 2 10:00 UTC; the next was due Oct 2 10:05 UTC · 4000 of 25920 candles'
		);
		expect(datasetProblemIndex([item])).toEqual({ 'SOL-USDC|5m': 'Backfill stuck' });
	});

	it('indexes the worst problem per dataset for Data health', () => {
		const items = datasetAttention({
			rows: [
				row({ freshness_status: 'stale', gap_count: 1, missing_intervals: 3 }),
				row({ product_id: 'BTC-USDC', timeframe: '1h', worker_status: 'failed' }),
				row({ product_id: 'ETH-USDC', timeframe: '1h' })
			],
			ingestion: {},
			strategies: [],
			nowMs: NOW
		});
		expect(datasetProblemIndex(items)).toEqual({
			'SOL-USDC|5m': 'Stale',
			'BTC-USDC|1h': 'Latest attempt failed'
		});
	});

	it('leaves a backfill on schedule alone', () => {
		expect(
			datasetAttention({
				rows: [row({ watch_status: 'backfilling' })],
				ingestion: { 'SOL-USDC|5m': ingestion() },
				strategies: [],
				nowMs: NOW
			})
		).toEqual([]);
	});
});

describe('stuckBackfillReason', () => {
	it('allows one interval (at least 15 minutes) past the scheduled attempt', () => {
		const onTime = ingestion({
			last_attempt_at: '2026-10-02T11:30:00Z',
			next_attempt_at: '2026-10-02T11:50:00Z'
		});
		expect(stuckBackfillReason(onTime, NOW)).toBeNull();
		expect(stuckBackfillReason(onTime, Date.parse('2026-10-02T12:11:00Z'))).toContain(
			'No attempt since Oct 2 11:30 UTC'
		);
	});

	it('flags an attempt that has been running for over an hour', () => {
		expect(
			stuckBackfillReason(
				ingestion({ status: 'running', last_attempt_at: '2026-10-02T10:30:00Z' }),
				NOW
			)
		).toBe('An attempt has been running since Oct 2 10:30 UTC');
		expect(
			stuckBackfillReason(
				ingestion({ status: 'running', last_attempt_at: '2026-10-02T11:30:00Z' }),
				NOW
			)
		).toBeNull();
	});

	it('flags repeated failures while it retries', () => {
		expect(
			stuckBackfillReason(
				ingestion({
					status: 'running',
					last_attempt_at: '2026-10-02T11:58:00Z',
					failure: {
						code: 'provider_unavailable',
						message: 'Provider hole.',
						consecutive_failures: 3
					}
				}),
				NOW
			)
		).toBe('Retrying after 3 failures: Provider hole.');
	});
});

function job(overrides: Partial<ResearchJob> = {}): ResearchJob {
	return {
		job_id: 'job-1',
		kind: 'backtest',
		status: 'failed',
		created_at: '2026-10-01T09:00:00Z',
		updated_at: '2026-10-01T09:05:00Z',
		error_message: 'Dataset verification failed.',
		failed_phase: 'dataset_resolution',
		failed_detail: null,
		strategy_id: 'strategy-1',
		...overrides
	};
}

describe('researchAttention', () => {
	it('flags the newest job when it failed within the last week', () => {
		const [item, ...rest] = researchAttention(
			{
				checked: [
					{ strategyId: 'strategy-1', strategyName: 'EMA Trend Pullback', jobs: [job()] },
					{
						strategyId: 'strategy-2',
						strategyName: 'RSI Reversion',
						jobs: [job({ job_id: 'ok', status: 'completed' }), job({ job_id: 'old-fail' })]
					},
					{
						strategyId: 'strategy-3',
						strategyName: 'SOL Breakout',
						jobs: [job({ job_id: 'stale-fail', updated_at: '2026-09-20T00:00:00Z' })]
					}
				],
				unchecked: 0,
				unreadable: 0
			},
			NOW
		);
		expect(rest).toEqual([]);
		expect(item).toMatchObject({
			id: 'research:job-1',
			icon: 'research',
			label: 'Backtest failed · EMA Trend Pullback',
			detail: 'Failed during dataset resolution: Dataset verification failed. · Oct 1 09:05 UTC',
			action: { label: 'Open Test', href: '/strategies/strategy-1/test' }
		});
	});

	it('labels studies and falls back when no reason was recorded', () => {
		const [item] = researchAttention(
			{
				checked: [
					{
						strategyId: 's',
						strategyName: 'Grid',
						jobs: [job({ kind: 'study', error_message: null, failed_phase: null })]
					}
				],
				unchecked: 0,
				unreadable: 0
			},
			NOW
		);
		expect(item.label).toBe('Study failed · Grid');
		expect(item.detail).toBe('The job failed without a recorded reason. · Oct 1 09:05 UTC');
	});
});

describe('sortAttention and attentionBotCount', () => {
	const make = (overrides: Partial<AttentionItem>): AttentionItem => ({
		id: 'x',
		category: 'bot',
		severity: 'warning',
		icon: 'pause',
		label: 'x',
		detail: '',
		action: { label: 'Review', href: '/deployments/x' },
		live: false,
		...overrides
	});

	it('puts critical first, then real money, then setup · bots · data · research', () => {
		const sorted = sortAttention([
			make({ id: 'research', category: 'research', label: 'a' }),
			make({ id: 'data', category: 'data', label: 'a' }),
			make({ id: 'paper-bot', label: 'b' }),
			make({ id: 'live-bot', live: true, label: 'z' }),
			make({ id: 'critical', severity: 'critical', label: 'z' }),
			make({ id: 'setup', category: 'setup', label: 'z' })
		]);
		expect(ids(sorted)).toEqual(['critical', 'live-bot', 'setup', 'paper-bot', 'data', 'research']);
	});

	it('counts distinct bots, not items', () => {
		expect(
			attentionBotCount([
				make({ id: 'a', deploymentId: 'one' }),
				make({ id: 'b', deploymentId: 'one' }),
				make({ id: 'c', deploymentId: 'two' }),
				make({ id: 'd', category: 'data' })
			])
		).toBe(2);
	});
});
