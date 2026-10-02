/**
 * Mocked `thytrader-bar-decision-v1` rows and trade reasons for the decision
 * timeline e2e suites (bot detail and the strategy Why stage).
 */
import type { Page, Route } from '@playwright/test';

export type Json = Record<string, unknown>;

export function comparison(overrides: Json = {}): Json {
	return {
		node: 'comparison',
		result: 'false',
		label: 'RSI(14) ≥ 50',
		operator: 'greater_than_or_equal',
		operator_symbol: '≥',
		left: {
			kind: 'indicator',
			label: 'RSI(14)',
			key: 'rsi_14',
			value: '47.2134',
			previous_value: null
		},
		right: { kind: 'literal', label: '50', key: null, value: '50', previous_value: null },
		...overrides
	};
}

export function crossover(result: 'true' | 'false' | 'unknown' = 'true'): Json {
	return {
		node: 'comparison',
		result,
		label: 'EMA(20) crosses above EMA(50)',
		operator: 'crosses_above',
		operator_symbol: 'crosses above',
		left: {
			kind: 'indicator',
			label: 'EMA(20)',
			key: 'ema_fast',
			value: '7.1234',
			previous_value: '7.0512'
		},
		right: {
			kind: 'indicator',
			label: 'EMA(50)',
			key: 'ema_slow',
			value: '7.1000',
			previous_value: '7.0800'
		}
	};
}

/** Entry rule with an HTF filter and the indicator values the bar saw. */
export function rule(
	outcome: 'matched' | 'not_matched' | 'undefined',
	entry: Json,
	candleStartsAt: string
): Json {
	return {
		outcome,
		entry,
		htf_filter: {
			timeframe: '4h',
			outcome: 'matched',
			condition: comparison({
				result: 'true',
				label: 'Close > EMA(200)',
				operator: 'greater_than',
				operator_symbol: '>',
				left: {
					kind: 'indicator',
					label: 'Close',
					key: 'close',
					value: '7.12',
					previous_value: null
				},
				right: {
					kind: 'indicator',
					label: 'EMA(200)',
					key: 'ema_200',
					value: '6.9',
					previous_value: null
				}
			})
		},
		signal: {
			candle_starts_at: candleStartsAt,
			indicator_values: [
				{ indicator_id: 'rsi_14', value: '47.2134' },
				{ indicator_id: 'ema_fast', value: '7.1234' },
				{ indicator_id: 'ema_slow', value: null }
			],
			entry_condition: outcome
		}
	};
}

const HOUR = 3_600_000;

/** ISO start of the 2h bar that closes `closesHoursBefore` hours before 2026-09-21 20:00 UTC. */
function bar(closesHoursBefore: number): { starts: string; closes: string; evaluated: string } {
	const closes = Date.UTC(2026, 8, 21, 20) - closesHoursBefore * HOUR;
	return {
		starts: new Date(closes - 2 * HOUR).toISOString().replace('.000Z', 'Z'),
		closes: new Date(closes).toISOString().replace('.000Z', 'Z'),
		evaluated: new Date(closes + 3_000).toISOString().replace('.000Z', 'Z')
	};
}

export function barDecision(deploymentId: string, overrides: Json = {}): Json {
	const times = bar(0);
	return {
		schema_version: 'thytrader-bar-decision-v1',
		deployment_id: deploymentId,
		strategy_id: '01a0ad42-0000-0000-0000-000000000000',
		strategy_fingerprint: `sha256:${'a'.repeat(64)}`,
		product_id: 'UNI-USDC',
		timeframe: '2h',
		mode: 'paper',
		bar_starts_at: times.starts,
		bar_closes_at: times.closes,
		evaluated_at: times.evaluated,
		outcome: 'no_signal',
		reason_code: 'CONDITIONS_NOT_MET',
		summary: 'No trade: RSI(14) 47.21 needs ≥ 50',
		skip_reason: null,
		exit_reason: null,
		action: 'none',
		intent_id: null,
		order_ids: [],
		orders: [],
		fills: [],
		close_price: '7.1234',
		rule: rule(
			'not_matched',
			{ node: 'all', result: 'false', children: [comparison()] },
			times.starts
		),
		risk: null,
		position: null,
		...overrides
	};
}

/** Overrides that place a row on the 2h bar closing `hoursBefore` hours before the newest. */
export function at(hoursBefore: number): Json {
	const times = bar(hoursBefore);
	return {
		bar_starts_at: times.starts,
		bar_closes_at: times.closes,
		evaluated_at: times.evaluated
	};
}

export const ENTRY_INTENT = '0199aaaa-0000-0000-0000-0000000000e1';
export const EXIT_INTENT = '0199aaaa-0000-0000-0000-0000000000e2';
export const EARLIER_INTENT = '0199aaaa-0000-0000-0000-0000000000e0';

/** One row per outcome, newest first, telling one paper trade's story. */
export function everyOutcome(deploymentId: string): Json[] {
	const entryTimes = bar(4);
	return [
		barDecision(deploymentId, {
			...at(0),
			outcome: 'exit',
			reason_code: 'EXIT_TARGET',
			summary: 'Exit: take profit filled at 7.70',
			exit_reason: 'target',
			action: 'none',
			intent_id: null,
			order_ids: ['0199bbbb-0000-0000-0000-0000000000a2'],
			orders: [
				{
					order_id: '0199bbbb-0000-0000-0000-0000000000a2',
					intent_id: EXIT_INTENT,
					purpose: 'take_profit',
					side: 'sell',
					kind: 'post_only_limit',
					status: 'filled',
					quantity: '5',
					price: '7.70',
					filled_quantity: '5',
					created_at: entryTimes.evaluated
				}
			],
			fills: [
				{
					fill_id: 'f-exit',
					order_id: '0199bbbb-0000-0000-0000-0000000000a2',
					purpose: 'take_profit',
					side: 'sell',
					price: '7.70',
					quantity: '5',
					fee: '0.0385',
					filled_at: '2026-09-21T19:41:00Z'
				}
			],
			rule: null,
			position: null
		}),
		barDecision(deploymentId, {
			...at(2),
			outcome: 'holding',
			reason_code: 'HOLDING',
			summary: 'Holding long 5 UNI; stop 6.80, target 7.70',
			rule: null,
			position: {
				side: 'long',
				quantity: '5',
				entry_price: '7.10',
				stop_price: '6.80',
				target_price: '7.70'
			}
		}),
		barDecision(deploymentId, {
			...at(4),
			outcome: 'entry_signal',
			reason_code: 'SIGNAL_MATCHED',
			summary: 'Entry: RSI(14) 55.20 ≥ 50 and EMA(20) crossed above EMA(50)',
			action: 'order_submitted',
			intent_id: ENTRY_INTENT,
			order_ids: ['0199bbbb-0000-0000-0000-0000000000a1'],
			orders: [
				{
					order_id: '0199bbbb-0000-0000-0000-0000000000a1',
					intent_id: ENTRY_INTENT,
					purpose: 'entry',
					side: 'buy',
					kind: 'post_only_limit',
					status: 'filled',
					quantity: '5',
					price: '7.10',
					filled_quantity: '5',
					created_at: entryTimes.evaluated
				}
			],
			fills: [
				{
					fill_id: 'f-entry',
					order_id: '0199bbbb-0000-0000-0000-0000000000a1',
					purpose: 'entry',
					side: 'buy',
					price: '7.10',
					quantity: '5',
					fee: '0.0355',
					filled_at: '2026-09-21T16:20:00Z'
				}
			],
			risk: { decision: 'allow', reason_code: 'ALLOWED', detail: '' },
			rule: rule(
				'matched',
				{
					node: 'all',
					result: 'true',
					children: [
						comparison({
							result: 'true',
							left: {
								kind: 'indicator',
								label: 'RSI(14)',
								key: 'rsi_14',
								value: '55.2',
								previous_value: null
							}
						}),
						crossover('true')
					]
				},
				entryTimes.starts
			),
			position: {
				side: 'long',
				quantity: '5',
				entry_price: '7.10',
				stop_price: '6.80',
				target_price: '7.70'
			}
		}),
		barDecision(deploymentId, {
			...at(6),
			outcome: 'entry_blocked',
			reason_code: 'MAX_PORTFOLIO_EXPOSURE',
			summary: 'Entry blocked by risk: portfolio exposure limit',
			risk: {
				decision: 'deny',
				reason_code: 'MAX_PORTFOLIO_EXPOSURE',
				detail: 'Exposure would exceed 10% of equity.'
			},
			rule: rule(
				'matched',
				{ node: 'any', result: 'true', children: [crossover('true')] },
				bar(6).starts
			)
		}),
		barDecision(deploymentId, {
			...at(8),
			outcome: 'skipped',
			reason_code: 'COOLDOWN',
			summary: 'Skipped: cooldown after the last trade (2 bars left)',
			skip_reason: 'cooldown',
			rule: null
		}),
		barDecision(deploymentId, {
			...at(10),
			outcome: 'no_signal',
			summary: 'No trade: RSI(14) 47.21 needs ≥ 50'
		}),
		barDecision(deploymentId, {
			...at(12),
			outcome: 'error',
			reason_code: 'EVALUATION_FAILED',
			summary: 'Could not evaluate: indicator inputs unavailable',
			rule: rule(
				'undefined',
				{
					node: 'not',
					result: 'unknown',
					children: [
						comparison({
							result: 'unknown',
							left: {
								kind: 'indicator',
								label: 'RSI(14)',
								key: 'rsi_14',
								value: null,
								previous_value: null
							}
						})
					]
				},
				bar(12).starts
			)
		})
	];
}

/** A persisted trade reason for one intent of one deployment. */
export function tradeReason(deploymentId: string, intentId: string, overrides: Json = {}): Json {
	return {
		schema_version: 'thytrader-trade-reason-v1',
		id: `reason-${intentId.slice(-2)}`,
		created_at: '2026-09-21T16:00:05Z',
		origin: 'runtime',
		intent_id: intentId,
		deployment_id: deploymentId,
		deployment_kind: 'strategy',
		mode: 'paper',
		product_id: 'UNI-USDC',
		purpose: 'entry',
		side: 'buy',
		strategy: null,
		signal: {
			kind: 'strategy_entry',
			last_signal: 'matched',
			candle_starts_at: '2026-09-21T14:00:00Z',
			timeframe: '2h'
		},
		risk: {
			decision: 'allow',
			reason_code: 'within_limits',
			detail: '',
			policy_fingerprint: `sha256:${'7'.repeat(64)}`,
			policy_source: 'published'
		},
		notes: [
			{
				origin: 'human',
				body: 'Breakout confirmed on volume.',
				recorded_at: '2026-09-21T16:30:00Z'
			}
		],
		reconcile: {
			order_id: '0199bbbb-0000-0000-0000-0000000000a1',
			order_status: 'filled',
			filled_quantity: '5',
			reject_reason: null,
			unknown_timeout: false,
			ledger_available: true,
			fills: [
				{
					fill_id: 'f-entry',
					price: '7.10',
					quantity: '5',
					fee: '0.0355',
					filled_at: '2026-09-21T16:20:00Z'
				}
			]
		},
		...overrides
	};
}

export function decisionPageBody(rows: Json[], overrides: Json = {}): Json {
	return {
		decisions: rows,
		limit: 50,
		returned: rows.length,
		next_cursor: null,
		storage: 'available',
		...overrides
	};
}

/** Every requested `outcome` value (repeated query parameter), in order. */
export function requestedOutcomes(route: Route): string[] {
	return new URL(route.request().url()).searchParams.getAll('outcome');
}

/** Rows whose outcome the request asked for (all rows when it named none). */
export function filterByRequest(route: Route, rows: Json[]): Json[] {
	const outcomes = requestedOutcomes(route);
	return outcomes.length === 0
		? rows
		: rows.filter((row) => outcomes.includes(String(row.outcome)));
}

/** `GET /api/v1/memory/trade-reasons?deployment_id=` answered per deployment. */
export async function mockTradeReasons(
	page: Page,
	byDeployment: (deploymentId: string) => Json[]
): Promise<string[]> {
	const requested: string[] = [];
	await page.route(
		(url) => url.pathname === '/api/v1/memory/trade-reasons',
		(route) => {
			const id = new URL(route.request().url()).searchParams.get('deployment_id') ?? '';
			requested.push(id);
			return route.fulfill({ json: { trade_reasons: byDeployment(id) } });
		}
	);
	return requested;
}
