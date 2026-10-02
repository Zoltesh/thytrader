import { afterEach, describe, expect, it, vi } from 'vitest';

import {
	DECISION_FILTERS,
	DecisionApiError,
	appendDecisionPage,
	assignTradeReasons,
	barSpanText,
	conditionChipText,
	conditionChipTitle,
	conditionGroupDescription,
	conditionGroupText,
	conditionOperandText,
	decisionActionLabel,
	decisionBotLabel,
	decisionEmptyText,
	decisionFilterOutcomes,
	decisionIntentIds,
	decisionKey,
	decisionOutcomeLabel,
	decisionOutcomeTone,
	decisionProducts,
	deploymentDecisionsPath,
	exitReasonLabel,
	fetchDeploymentDecisions,
	fetchStrategyDecisions,
	formatDecimalCompact,
	htfFilterChipText,
	intentPurposeLabel,
	nextEvaluationAt,
	nextEvaluationText,
	positionSnapshotText,
	riskVerdictText,
	ruleOutcomeLabel,
	ruleOutcomeResult,
	skipReasonLabel,
	strategyDecisionsPath,
	validateDecisionPage,
	type BarDecision,
	type ComparisonNode,
	type ConditionOperand,
	type DecisionOrder,
	type DecisionOutcome
} from './decisions';
import type { TradeReasonRecord } from './memory';

function operand(overrides: Partial<ConditionOperand> = {}): ConditionOperand {
	return {
		kind: 'indicator',
		label: 'RSI(14)',
		key: 'rsi_14',
		value: '47.2134',
		previous_value: null,
		...overrides
	};
}

function literal(value: string): ConditionOperand {
	return { kind: 'literal', label: value, key: null, value, previous_value: null };
}

function comparison(overrides: Partial<ComparisonNode> = {}): ComparisonNode {
	return {
		node: 'comparison',
		result: 'false',
		label: 'RSI(14) ≥ 50',
		operator: 'greater_than_or_equal',
		operator_symbol: '≥',
		left: operand(),
		right: literal('50'),
		...overrides
	};
}

function decision(overrides: Partial<BarDecision> = {}): BarDecision {
	return {
		schema_version: 'thytrader-bar-decision-v1',
		deployment_id: 'dep-1',
		strategy_id: 'strat-1',
		strategy_fingerprint: `sha256:${'a'.repeat(64)}`,
		product_id: 'UNI-USDC',
		timeframe: '2h',
		mode: 'paper',
		bar_starts_at: '2026-09-21T18:00:00Z',
		bar_closes_at: '2026-09-21T20:00:00Z',
		evaluated_at: '2026-09-21T20:00:03Z',
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
		rule: null,
		risk: null,
		position: null,
		...overrides
	};
}

function order(overrides: Partial<DecisionOrder> = {}): DecisionOrder {
	return {
		order_id: 'o-1',
		intent_id: 'i-1',
		purpose: 'entry',
		side: 'buy',
		kind: 'post_only_limit',
		status: 'open',
		quantity: '5',
		price: '7.10',
		filled_quantity: '0',
		created_at: '2026-09-21T20:00:04Z',
		...overrides
	};
}

function reason(intentId: string, id = `r-${intentId}`): TradeReasonRecord {
	return {
		schema_version: 'thytrader-trade-reason-v1',
		id,
		created_at: '2026-09-21T20:00:05Z',
		origin: 'runtime',
		intent_id: intentId,
		deployment_id: 'dep-1',
		deployment_kind: 'strategy',
		mode: 'paper',
		product_id: 'UNI-USDC',
		purpose: 'entry',
		side: 'buy',
		strategy: null,
		signal: {
			kind: 'strategy_entry',
			last_signal: 'matched',
			candle_starts_at: '2026-09-21T18:00:00Z',
			timeframe: '2h'
		},
		risk: {
			decision: 'allow',
			reason_code: 'ALLOWED',
			detail: '',
			policy_fingerprint: 'sha256:p',
			policy_source: 'published'
		},
		notes: [],
		reconcile: {
			order_id: null,
			order_status: null,
			filled_quantity: null,
			reject_reason: null,
			unknown_timeout: false,
			ledger_available: true,
			fills: []
		}
	};
}

function pageBody(rows: BarDecision[], overrides: Record<string, unknown> = {}) {
	return {
		deployment_id: 'dep-1',
		decisions: rows,
		limit: 50,
		returned: rows.length,
		next_cursor: null,
		storage: 'available',
		...overrides
	};
}

function okResponse(body: unknown) {
	return { ok: true, status: 200, json: async () => body };
}

function failedResponse(status: number, body: unknown) {
	return {
		ok: false,
		status,
		json: async () => {
			if (body === undefined) throw new SyntaxError('no body');
			return body;
		}
	};
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('outcome chips', () => {
	it('labels every outcome and gives it a tone; the label always carries the meaning', () => {
		const outcomes: DecisionOutcome[] = [
			'entry_signal',
			'no_signal',
			'holding',
			'exit',
			'entry_blocked',
			'skipped',
			'error'
		];
		expect(outcomes.map(decisionOutcomeLabel)).toEqual([
			'Entry',
			'No signal',
			'Holding',
			'Exit',
			'Blocked',
			'Skipped',
			'Error'
		]);
		expect(outcomes.map(decisionOutcomeTone)).toEqual([
			'pos',
			'muted',
			'info',
			'info',
			'warn',
			'muted',
			'neg'
		]);
	});

	it('shows an unexpected future outcome as raw text instead of failing', () => {
		expect(decisionOutcomeLabel('partial_exit' as DecisionOutcome)).toBe('partial exit');
		expect(decisionOutcomeTone('partial_exit' as DecisionOutcome)).toBe('muted');
	});

	it('words skip reasons, exit reasons, actions, and rule outcomes', () => {
		expect(skipReasonLabel('cooldown')).toBe('cooldown after the last trade');
		expect(skipReasonLabel('user_feed_gate')).toBe('user-order feed not connected');
		expect(exitReasonLabel('target')).toBe('take profit');
		expect(exitReasonLabel('trail')).toBe('trailing stop');
		expect(decisionActionLabel('none')).toBe('No order action');
		expect(decisionActionLabel('intent_created')).toBe('Order intent persisted');
		expect(decisionActionLabel('repriced')).toBe('Order repriced');
		expect(ruleOutcomeLabel('not_matched')).toBe('not matched');
		expect(ruleOutcomeLabel('undefined')).toBe('could not be evaluated');
		expect(ruleOutcomeResult('matched')).toBe('true');
		expect(ruleOutcomeResult('not_matched')).toBe('false');
		expect(ruleOutcomeResult('undefined')).toBe('unknown');
		expect(intentPurposeLabel('take_profit')).toBe('take profit');
		expect(intentPurposeLabel(null)).toBe('—');
	});
});

describe('filters and request paths', () => {
	it('maps each filter to its repeated outcome values', () => {
		expect(DECISION_FILTERS.map((filter) => filter.label)).toEqual([
			'All',
			'Trades',
			'Blocked',
			'No signal'
		]);
		expect(decisionFilterOutcomes('all')).toEqual([]);
		expect(decisionFilterOutcomes('trades')).toEqual(['entry_signal', 'exit']);
		expect(decisionFilterOutcomes('blocked')).toEqual(['entry_blocked']);
		expect(decisionFilterOutcomes('no_signal')).toEqual(['no_signal']);
	});

	it('builds deployment paths with cursor, repeated outcomes, and product', () => {
		expect(deploymentDecisionsPath('dep-1')).toBe('/api/v1/deployments/dep-1/decisions?limit=50');
		expect(
			deploymentDecisionsPath('dep-1', {
				limit: 25,
				cursor: 'opaque/cursor+1',
				outcomes: decisionFilterOutcomes('trades'),
				productId: 'UNI-USDC'
			})
		).toBe(
			'/api/v1/deployments/dep-1/decisions?limit=25&cursor=opaque%2Fcursor%2B1&outcome=entry_signal&outcome=exit&product_id=UNI-USDC'
		);
		// No outcome parameter at all for "All"; a null cursor is the newest page.
		expect(deploymentDecisionsPath('dep-1', { cursor: null, outcomes: [] })).toBe(
			'/api/v1/deployments/dep-1/decisions?limit=50'
		);
	});

	it('builds strategy paths with the optional deployment filter', () => {
		expect(strategyDecisionsPath('strat 1')).toBe(
			'/api/v1/strategies/strat%201/decisions?limit=50'
		);
		expect(
			strategyDecisionsPath('strat-1', {
				deploymentId: 'dep-2',
				outcomes: decisionFilterOutcomes('blocked'),
				cursor: 'c-2'
			})
		).toBe(
			'/api/v1/strategies/strat-1/decisions?limit=50&cursor=c-2&outcome=entry_blocked&deployment_id=dep-2'
		);
		expect(strategyDecisionsPath('strat-1', { deploymentId: null })).toBe(
			'/api/v1/strategies/strat-1/decisions?limit=50'
		);
	});

	it('clamps the page size to the API bound of 1..200', () => {
		expect(deploymentDecisionsPath('d', { limit: 0 })).toContain('limit=1');
		expect(deploymentDecisionsPath('d', { limit: 500 })).toContain('limit=200');
		expect(deploymentDecisionsPath('d', { limit: 20.7 })).toContain('limit=20');
		expect(deploymentDecisionsPath('d', { limit: Number.NaN })).toContain('limit=50');
	});
});

describe('decision fetchers', () => {
	it('reads one deployment page with the JSON accept header', async () => {
		const row = decision();
		const fetchMock = vi.fn().mockResolvedValue(okResponse(pageBody([row], { next_cursor: 'c1' })));
		vi.stubGlobal('fetch', fetchMock);
		const page = await fetchDeploymentDecisions('dep-1', {
			outcomes: decisionFilterOutcomes('no_signal')
		});
		expect(fetchMock).toHaveBeenCalledWith(
			'/api/v1/deployments/dep-1/decisions?limit=50&outcome=no_signal',
			{ headers: { Accept: 'application/json' } }
		);
		expect(page).toEqual({ decisions: [row], nextCursor: 'c1', storage: 'available' });
	});

	it('reads one strategy page narrowed to a deployment', async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			okResponse({
				...pageBody([]),
				strategy_id: 'strat-1',
				deployment_id: 'dep-9',
				storage: 'unavailable'
			})
		);
		vi.stubGlobal('fetch', fetchMock);
		const page = await fetchStrategyDecisions('strat-1', {
			deploymentId: 'dep-9',
			cursor: 'next',
			limit: 10
		});
		expect(fetchMock.mock.calls[0]?.[0]).toBe(
			'/api/v1/strategies/strat-1/decisions?limit=10&cursor=next&deployment_id=dep-9'
		);
		expect(page.storage).toBe('unavailable');
		expect(page.decisions).toEqual([]);
	});

	it('surfaces the server detail with the HTTP status', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(failedResponse(404, { detail: 'Deployment not found.' }))
		);
		const caught = await fetchDeploymentDecisions('missing').catch((error: unknown) => error);
		expect(caught).toBeInstanceOf(DecisionApiError);
		expect((caught as DecisionApiError).status).toBe(404);
		expect((caught as DecisionApiError).message).toBe('Deployment not found.');
	});

	it('reads structured details and falls back per status when the body is unreadable', async () => {
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValueOnce(
					failedResponse(503, { detail: { code: 'storage', message: 'Journal store failed.' } })
				)
				.mockResolvedValueOnce(failedResponse(400, undefined))
				.mockResolvedValueOnce(failedResponse(503, { detail: '' }))
		);
		await expect(fetchDeploymentDecisions('d')).rejects.toThrow('Journal store failed.');
		await expect(fetchDeploymentDecisions('d', { cursor: 'bad' })).rejects.toThrow(
			/invalid cursor/
		);
		await expect(fetchDeploymentDecisions('d')).rejects.toThrow(
			'Decision storage is temporarily unavailable.'
		);
	});
});

describe('page contract checks', () => {
	it('fails closed on protocol violations', () => {
		expect(() => validateDecisionPage({ storage: 'available', next_cursor: null }, 50)).toThrow(
			/missing its decisions page/
		);
		expect(() => validateDecisionPage(pageBody([decision(), decision()]), 1)).toThrow(
			/2 decisions for a 1-row page/
		);
		expect(() => validateDecisionPage(pageBody([decision()], { returned: 3 }), 50)).toThrow(
			/counted 3 rows but sent 1/
		);
		expect(() => validateDecisionPage(pageBody([], { storage: 'maybe' }), 50)).toThrow(
			/storage is available/
		);
		expect(() => validateDecisionPage(pageBody([], { next_cursor: 'more' }), 50)).toThrow(
			/empty decision page/
		);
		expect(() => validateDecisionPage(pageBody([], { next_cursor: 7 }), 50)).toThrow(
			/unreadable cursor/
		);
	});

	it('accepts an honest empty page without storage', () => {
		expect(validateDecisionPage(pageBody([], { storage: 'unavailable' }), 50)).toEqual({
			decisions: [],
			nextCursor: null,
			storage: 'unavailable'
		});
	});

	it('appends older pages without showing a bar twice', () => {
		const newest = decision({ bar_starts_at: '2026-09-21T18:00:00Z' });
		const older = decision({ bar_starts_at: '2026-09-21T16:00:00Z' });
		const otherProduct = decision({
			bar_starts_at: '2026-09-21T16:00:00Z',
			product_id: 'ETH-USDC'
		});
		expect(decisionKey(newest)).toBe('dep-1|UNI-USDC|2026-09-21T18:00:00Z');
		const merged = appendDecisionPage([newest], [newest, older, otherProduct, older]);
		expect(merged.map(decisionKey)).toEqual([
			'dep-1|UNI-USDC|2026-09-21T18:00:00Z',
			'dep-1|UNI-USDC|2026-09-21T16:00:00Z',
			'dep-1|ETH-USDC|2026-09-21T16:00:00Z'
		]);
		expect(decisionProducts(merged)).toEqual(['UNI-USDC', 'ETH-USDC']);
	});
});

describe('condition chips', () => {
	it('shows actual values against thresholds with a pass/fail mark', () => {
		expect(conditionChipText(comparison())).toBe('RSI(14) 47.21 ≥ 50 ✗');
		expect(conditionChipText(comparison({ result: 'true', left: operand({ value: '55' }) }))).toBe(
			'RSI(14) 55 ≥ 50 ✓'
		);
	});

	it('shows previous→current for crossovers on both indicator sides', () => {
		const node = comparison({
			result: 'true',
			label: 'EMA(20) crosses above EMA(50)',
			operator: 'crosses_above',
			operator_symbol: 'crosses above',
			left: operand({ label: 'EMA(20)', value: '103.5', previous_value: '101.2' }),
			right: operand({ label: 'EMA(50)', value: '102.4', previous_value: '102.0' })
		});
		expect(conditionChipText(node)).toBe('EMA(20) 101.2→103.5 crosses above EMA(50) 102→102.4 ✓');
		expect(conditionChipTitle(node)).toBe(
			'EMA(20) crosses above EMA(50) (met): EMA(20) = 101.2 → 103.5; EMA(50) = 102.0 → 102.4'
		);
	});

	it('marks undefined values and unknown results honestly', () => {
		const node = comparison({ result: 'unknown', left: operand({ value: null }) });
		expect(conditionChipText(node)).toBe('RSI(14) n/a ≥ 50 ?');
		expect(conditionChipTitle(node)).toContain('RSI(14) = undefined');
		expect(conditionOperandText(operand({ value: null, previous_value: null }), true)).toBe(
			'RSI(14) n/a→n/a'
		);
		// A literal without a value falls back to its label.
		expect(conditionOperandText({ ...literal('65'), value: null }, false)).toBe('65');
	});

	it('names groups and what they require', () => {
		const group = { node: 'all' as const, result: 'false' as const, children: [comparison()] };
		expect(conditionGroupText(group)).toBe('ALL ✗');
		expect(conditionGroupDescription(group)).toBe('every condition below must hold: not met');
		expect(conditionGroupText({ ...group, node: 'any', result: 'true' })).toBe('ANY ✓');
		expect(conditionGroupDescription({ ...group, node: 'not', result: 'unknown' })).toBe(
			'the condition below must not hold: unknown'
		);
		expect(
			htfFilterChipText({ timeframe: '4h', outcome: 'matched', condition: comparison() })
		).toBe('HTF 4h filter matched ✓');
	});
});

describe('compact decimals', () => {
	it('keeps two decimals and four significant digits, trimming zeros', () => {
		expect(formatDecimalCompact('47.2134')).toBe('47.21');
		expect(formatDecimalCompact('7.1234')).toBe('7.123');
		expect(formatDecimalCompact('63412.1250')).toBe('63412.13');
		expect(formatDecimalCompact('0.000012345')).toBe('0.00001235');
		expect(formatDecimalCompact('0.0381')).toBe('0.0381');
		expect(formatDecimalCompact('50')).toBe('50');
		expect(formatDecimalCompact('50.000')).toBe('50');
		expect(formatDecimalCompact('101.20')).toBe('101.2');
	});

	it('rounds half up across the decimal point and keeps the sign', () => {
		expect(formatDecimalCompact('0.99999')).toBe('1');
		expect(formatDecimalCompact('9.9996')).toBe('10');
		expect(formatDecimalCompact('-12.345')).toBe('-12.35');
		expect(formatDecimalCompact('-0.5')).toBe('-0.5');
		expect(formatDecimalCompact('-0.000')).toBe('0');
		expect(formatDecimalCompact('+3.5')).toBe('3.5');
	});

	it('expands exponent forms and passes non-decimals through', () => {
		expect(formatDecimalCompact('1E-7')).toBe('0.0000001');
		expect(formatDecimalCompact('1.5E+3')).toBe('1500');
		expect(formatDecimalCompact('0E-8')).toBe('0');
		expect(formatDecimalCompact('.5')).toBe('0.5');
		expect(formatDecimalCompact('NaN')).toBe('NaN');
		expect(formatDecimalCompact('1E+999')).toBe('1E+999');
		expect(formatDecimalCompact(null)).toBe('—');
		expect(formatDecimalCompact(undefined)).toBe('—');
		expect(formatDecimalCompact('  ')).toBe('—');
	});
});

describe('next evaluation', () => {
	const bar = '2026-09-21T20:00:00Z';

	it('is the close of the bar after the last evaluated bar start', () => {
		const at = (timeframe: string): string | undefined =>
			nextEvaluationAt({ last_evaluated_bar: bar, timeframe })?.toISOString();
		expect(at('1m')).toBe('2026-09-21T20:02:00.000Z');
		expect(at('5m')).toBe('2026-09-21T20:10:00.000Z');
		expect(at('15m')).toBe('2026-09-21T20:30:00.000Z');
		expect(at('2h')).toBe('2026-09-22T00:00:00.000Z');
		expect(at('6h')).toBe('2026-09-22T08:00:00.000Z');
		expect(
			nextEvaluationAt({
				last_evaluated_bar: '2026-09-20T00:00:00+00:00',
				timeframe: '1d'
			})?.toISOString()
		).toBe('2026-09-22T00:00:00.000Z');
	});

	it('is unknown before the first evaluation or without a parseable clock', () => {
		expect(nextEvaluationAt({ last_evaluated_bar: null, timeframe: '1h' })).toBeNull();
		expect(nextEvaluationAt({ last_evaluated_bar: bar, timeframe: null })).toBeNull();
		expect(nextEvaluationAt({ last_evaluated_bar: bar, timeframe: 'weekly' })).toBeNull();
		expect(nextEvaluationAt({ last_evaluated_bar: 'not a time', timeframe: '1h' })).toBeNull();
	});

	it('words the header line honestly for every state', () => {
		expect(
			nextEvaluationText({ last_evaluated_bar: bar, timeframe: '2h', status: 'running' })
		).toBe('Next evaluation ≈ 2026-09-22 00:00 UTC');
		expect(nextEvaluationText({ last_evaluated_bar: bar, timeframe: '1h', status: 'paused' })).toBe(
			'Next evaluation ≈ 2026-09-21 22:00 UTC'
		);
		expect(
			nextEvaluationText({ last_evaluated_bar: null, timeframe: '15m', status: 'running' })
		).toBe('Next evaluation ≈ after the next 15m bar closes');
		expect(
			nextEvaluationText({ last_evaluated_bar: bar, timeframe: '2h', status: 'stopped' })
		).toBe('Stopped: bars are evaluated only while residual exposure remains');
		expect(
			nextEvaluationText({ last_evaluated_bar: bar, timeframe: null, status: 'running' })
		).toBe('Next evaluation: — (bar clock unknown)');
	});
});

describe('trade reasons joined by intent', () => {
	it('gives each reason exactly one owning row and lists the rest as unmatched', () => {
		const repriced = decision({
			bar_starts_at: '2026-09-21T20:00:00Z',
			bar_closes_at: '2026-09-21T22:00:00Z',
			outcome: 'holding',
			action: 'repriced',
			intent_id: 'i-1',
			orders: [order()]
		});
		const created = decision({
			bar_starts_at: '2026-09-21T18:00:00Z',
			outcome: 'entry_signal',
			action: 'intent_created',
			intent_id: 'i-1'
		});
		const exitByOrder = decision({
			bar_starts_at: '2026-09-21T16:00:00Z',
			outcome: 'exit',
			orders: [order({ order_id: 'o-2', intent_id: 'i-2', purpose: 'take_profit' })]
		});
		const rows = [repriced, created, exitByOrder];
		const assignment = assignTradeReasons(rows, [reason('i-1'), reason('i-2'), reason('i-old')]);
		// The oldest row whose own intent matches owns i-1 (the bar that created it).
		expect(assignment.ownerByIntent.get('i-1')).toBe(decisionKey(created));
		expect(assignment.byDecision.get(decisionKey(created))?.map((r) => r.id)).toEqual(['r-i-1']);
		expect(assignment.byDecision.get(decisionKey(repriced))).toBeUndefined();
		// An intent linked only through an order is owned by that row.
		expect(assignment.ownerByIntent.get('i-2')).toBe(decisionKey(exitByOrder));
		expect(assignment.unmatched.map((r) => r.id)).toEqual(['r-i-old']);
		expect(decisionIntentIds(repriced)).toEqual(['i-1']);
		expect(decisionIntentIds(exitByOrder)).toEqual(['i-2']);
	});

	it('leaves every reason unmatched when no rows are loaded', () => {
		const assignment = assignTradeReasons([], [reason('i-1')]);
		expect(assignment.unmatched).toHaveLength(1);
		expect(assignment.byDecision.size).toBe(0);
	});
});

describe('row details', () => {
	it('spans the bar in UTC and keeps the close date across midnight', () => {
		expect(barSpanText(decision())).toBe('2026-09-21 18:00 → 20:00 UTC · 2h');
		expect(
			barSpanText(
				decision({
					timeframe: '1d',
					bar_starts_at: '2026-09-20T00:00:00Z',
					bar_closes_at: '2026-09-21T00:00:00Z'
				})
			)
		).toBe('2026-09-20 00:00 → 2026-09-21 00:00 UTC · 1d');
	});

	it('words risk, positions, empty filters, and bot labels', () => {
		expect(
			riskVerdictText({
				decision: 'deny',
				reason_code: 'MAX_PORTFOLIO_EXPOSURE',
				detail: 'Exposure would exceed 10%.'
			})
		).toBe('Risk denied (MAX_PORTFOLIO_EXPOSURE) · Exposure would exceed 10%.');
		expect(riskVerdictText({ decision: 'allow', reason_code: 'ALLOWED', detail: ' ' })).toBe(
			'Risk allowed (ALLOWED)'
		);
		expect(
			positionSnapshotText({
				side: 'long',
				quantity: '5',
				entry_price: '7.10',
				stop_price: '6.80',
				target_price: '7.70'
			})
		).toBe('long 5 @ 7.10 · stop 6.80 · target 7.70');
		expect(decisionEmptyText('all', '2h')).toBe(
			'No decisions journaled yet. A row is recorded after each completed 2h bar is evaluated.'
		);
		expect(decisionEmptyText('all', null)).toBe(
			'No decisions journaled yet. A row is recorded after each completed bar is evaluated.'
		);
		expect(decisionEmptyText('blocked', '2h')).toBe(
			'No blocked entries in the journaled decision history.'
		);
		expect(
			decisionBotLabel({ mode: 'live', product_id: 'ETH-USDC', created_at: '2026-09-24T09:00:00Z' })
		).toBe('LIVE · ETH / USDC · since 2026-09-24');
	});
});
