import { describe, expect, it } from 'vitest';

import {
	canOfferFlatten,
	drawdownIsCaveated,
	eligibility,
	exactVersionEvidenceLinks,
	fingerprintText,
	lifecycleAcceptedMessage,
	lifecycleDialog,
	lastEvaluatedText,
	ledgerPerformanceText,
	marketLabel,
	otherVersionDeployments,
	performanceCurrencySuffix,
	performanceReportText,
	productIdQuote,
	quoteAmountLabel,
	resolveRequestedFingerprint,
	resumeBreakerNote,
	strategyIdentityLink,
	strategyVersionLink,
	workingOrderCount
} from './deployment-detail';
import { summarizeStrategySource } from './strategy-config';
import {
	listAllDeployments,
	listDeploymentsPage,
	stopDeployment,
	type Deployment
} from './deployments';

function deployment(overrides: Partial<Deployment> = {}): Deployment {
	return {
		id: 'dep-1',
		strategy_fingerprint: 'sha256:' + 'a'.repeat(64),
		strategy_id: 'strat-1',
		kind: 'strategy',
		timeframe: '2h',
		product_id: 'UNI-USDC',
		mode: 'paper',
		status: 'running',
		phase: 'flat',
		cash: '10000',
		paper_starting_cash: '10000',
		last_evaluated_bar: null,
		last_signal: null,
		mismatch_detail: null,
		pending_entry_bars: 0,
		bars_held: 0,
		lifecycle_command: 'none',
		daily_loss_latched: false,
		drawdown_latched: false,
		revision: 1,
		worker_lease_held: false,
		created_at: '2026-09-16T00:00:00+00:00',
		updated_at: '2026-09-16T12:00:00+00:00',
		position: null,
		positions: [],
		orders: [],
		fills: [],
		...overrides
	};
}

describe('marketLabel', () => {
	it('reads the quote from the product id without blanket USD→USDC substitution', () => {
		expect(marketLabel('UNI-USDC')).toBe('UNI / USDC');
		expect(marketLabel('UNI-USD')).toBe('UNI / USD');
		expect(marketLabel('BTC-USD')).toBe('BTC / USD');
	});

	it('passes ids without a quote separator through unchanged', () => {
		expect(marketLabel('WEIRD')).toBe('WEIRD');
		expect(productIdQuote('WEIRD')).toBeNull();
	});
});

describe('quoteAmountLabel', () => {
	it('labels amounts with the actual quote currency of the product', () => {
		expect(quoteAmountLabel('10000', 'UNI-USDC')).toBe('10000 USDC');
		expect(quoteAmountLabel('10000', 'UNI-USD')).toBe('10000 USD');
		expect(quoteAmountLabel(null, 'UNI-USDC')).toBe('unknown');
	});
});

describe('eligibility', () => {
	it('derives eligibility from visible state only', () => {
		expect(eligibility(deployment({ status: 'running' })).eligibility).toBe('Eligible');
		expect(eligibility(deployment({ status: 'paused' })).eligibility).toBe('Blocked by pause');
		expect(eligibility(deployment({ status: 'stopped' })).eligibility).toBe('Stopped — no entries');
		expect(
			eligibility(deployment({ status: 'running', daily_loss_latched: true })).eligibility
		).toBe('Blocked by daily-loss breaker');
		expect(
			eligibility(
				deployment({ status: 'running', daily_loss_latched: true, drawdown_latched: true })
			).eligibility
		).toBe('Blocked by daily-loss and drawdown breakers');
	});

	it('labels the lifecycle instruction, never inventing one', () => {
		expect(eligibility(deployment({ lifecycle_command: 'managed_shutdown' })).instruction).toBe(
			'Managed stop'
		);
		expect(eligibility(deployment({ lifecycle_command: 'stop_new_entries' })).instruction).toBe(
			'Stop new entries'
		);
	});
});

describe('lifecycleDialog', () => {
	it('describes managed stop as protective and permanent', () => {
		const dialog = lifecycleDialog(deployment({ status: 'running' }), 'stop');
		expect(dialog.title).toBe('Stop paper deployment?');
		expect(dialog.body.join('\n')).toContain('cannot be resumed after it is stopped');
		expect(dialog.chooseStopMode).toBe(true);
	});

	it('resume stays safe when a breaker is latched: resume does not reset it', () => {
		const dialog = lifecycleDialog(
			deployment({ status: 'paused', daily_loss_latched: true }),
			'resume'
		);
		expect(dialog.body.join('\n')).toContain('Daily-loss breaker is latched');
		expect(dialog.body.join('\n')).toContain('until the latch is reset separately');
		expect(dialog.confirm).toBe('Resume entries');
		expect(resumeBreakerNote(deployment({ status: 'paused' }))).toBeNull();
	});

	it('flatten names marketable exits, no guaranteed price, and does not restart', () => {
		const dialog = lifecycleDialog(deployment({ status: 'stopped' }), 'flatten');
		expect(dialog.body.join('\n')).toContain('marketable exits');
		expect(dialog.body.join('\n')).toContain('Exit price and fees are not guaranteed');
		expect(dialog.body.join('\n')).toContain('does not restart');
	});

	it('live resume is marked as the risk-affecting path', () => {
		const dialog = lifecycleDialog(deployment({ status: 'paused', mode: 'live' }), 'resume');
		expect(dialog.danger).toBe(true);
	});
});

describe('canOfferFlatten', () => {
	it('requires stopped + managed_shutdown + residual exposure', () => {
		const position = {
			product_id: 'UNI-USDC',
			quantity: '1',
			entry_price: '10',
			stop_price: '9',
			target_price: '12',
			entered_bar: '2026-09-16T01:00:00+00:00',
			protection_status: 'protected'
		};
		expect(
			canOfferFlatten(
				deployment({
					status: 'stopped',
					lifecycle_command: 'managed_shutdown',
					positions: [position]
				})
			)
		).toBe(true);
		expect(
			canOfferFlatten(deployment({ status: 'stopped', lifecycle_command: 'managed_shutdown' }))
		).toBe(false);
		expect(
			canOfferFlatten(
				deployment({ status: 'stopped', lifecycle_command: 'flatten', positions: [position] })
			)
		).toBe(false);
		expect(
			canOfferFlatten(
				deployment({
					status: 'running',
					lifecycle_command: 'managed_shutdown',
					positions: [position]
				})
			)
		).toBe(false);
	});

	it('counts working orders as residual exposure', () => {
		const order = {
			id: 'o1',
			client_order_id: 'c1',
			venue_order_id: null,
			side: 'buy',
			kind: 'limit',
			quantity: '1',
			price: '10',
			filled_quantity: '0',
			status: 'open',
			reject_reason: null,
			created_at: '2026-09-16T00:00:00+00:00',
			updated_at: '2026-09-16T00:00:00+00:00'
		};
		expect(
			canOfferFlatten(
				deployment({ status: 'stopped', lifecycle_command: 'managed_shutdown', orders: [order] })
			)
		).toBe(true);
		expect(workingOrderCount(deployment({ orders: [order] }))).toBe(1);
	});
});

describe('version mixup guards', () => {
	it('separates same-strategy deployments running different fingerprints', () => {
		const current = deployment();
		const otherVersion = deployment({
			id: 'dep-2',
			strategy_fingerprint: 'sha256:' + 'b'.repeat(64)
		});
		const otherStrategy = deployment({
			id: 'dep-3',
			strategy_id: 'strat-9'
		});
		const sameVersion = deployment({ id: 'dep-4' });
		const others = otherVersionDeployments(
			[otherVersion, otherStrategy, sameVersion, current],
			current
		);
		expect(others.map((item) => item.id)).toEqual(['dep-2']);
	});

	it('builds a fingerprint-exact deploy link and a strategy-identity link', () => {
		const current = deployment();
		expect(strategyVersionLink(current)).toEqual({
			href: `/deploy?strategy=strat-1&strategy_fingerprint=${encodeURIComponent('sha256:' + 'a'.repeat(64))}`,
			fingerprint: 'sha256:' + 'a'.repeat(64)
		});
		expect(strategyIdentityLink(current)).toBe('/deploy?strategy=strat-1');
	});

	it('keeps discretionary rows honest: no invented fingerprint or link', () => {
		const discretionary = deployment({ strategy_fingerprint: null });
		expect(fingerprintText(discretionary)).toBe('unknown (discretionary)');
		expect(strategyVersionLink(discretionary)).toBeNull();
	});
});

describe('performance and evaluation text', () => {
	it('renders the ledger summary with the deployment quote currency', () => {
		const text = ledgerPerformanceText(
			deployment({
				ledger: {
					trade_count: 3,
					total_net_pnl: '12.5',
					total_return_fraction: '0.00125',
					mark_complete: true,
					marked_exposure: null
				}
			})
		);
		expect(text).toContain('3 closed trades');
		expect(text).toContain('12.5 USDC');
	});

	it('says when the ledger or net performance is unavailable', () => {
		expect(ledgerPerformanceText(deployment({ ledger: null }))).toBe('Ledger summary unavailable.');
		expect(
			ledgerPerformanceText(
				deployment({
					ledger: {
						trade_count: 0,
						total_net_pnl: null,
						total_return_fraction: null,
						mark_complete: false,
						marked_exposure: null
					}
				})
			)
		).toContain('Net performance not yet computed.');
	});

	it('states the last evaluated bar or its absence', () => {
		expect(lastEvaluatedText(deployment())).toBe('No completed bar evaluated yet.');
		expect(
			lastEvaluatedText(
				deployment({ last_evaluated_bar: '2026-09-21T20:00:00+00:00', last_signal: 'not_matched' })
			)
		).toContain('2026-09-21T20:00:00+00:00');
	});
});

describe('stop URL contract', () => {
	it('sends managed stop without the flatten flag and flatten with ?flatten=true', async () => {
		const calls: string[] = [];
		const original = globalThis.fetch;
		globalThis.fetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url === '/api/v1/security/session') {
				// CSRF bootstrap for the mutation lane; grant a session token.
				return new Response(JSON.stringify({ csrf_token: 'test-token' }), {
					status: 200,
					headers: { 'content-type': 'application/json' }
				});
			}
			calls.push(url);
			return new Response(JSON.stringify(deployment()), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as typeof fetch;
		try {
			await stopDeployment('dep-1');
			await stopDeployment('dep-1', false);
			await stopDeployment('dep-1', true);
		} finally {
			globalThis.fetch = original;
		}
		expect(calls).toEqual([
			'/api/v1/deployments/dep-1/stop',
			'/api/v1/deployments/dep-1/stop',
			'/api/v1/deployments/dep-1/stop?flatten=true'
		]);
	});
});

describe('inventory pagination', () => {
	it('pages by offset and reports hasMore only on a full page', async () => {
		const full = Array.from({ length: 50 }, (_, index) => deployment({ id: `dep-${index}` }));
		const calls: string[] = [];
		const original = globalThis.fetch;
		globalThis.fetch = (async (input: RequestInfo | URL) => {
			const url = new URL(String(input), 'http://local');
			calls.push(url.search);
			const offset = Number(url.searchParams.get('offset'));
			const rows = offset === 0 ? full : full.slice(0, 5);
			return new Response(
				JSON.stringify({ deployments: rows, limit: 50, offset, returned: rows.length }),
				{
					status: 200,
					headers: { 'content-type': 'application/json' }
				}
			);
		}) as typeof fetch;
		try {
			const first = await listDeploymentsPage(50, 0);
			const second = await listDeploymentsPage(50, 50);
			expect(first.hasMore).toBe(true);
			expect(second.hasMore).toBe(false);
			expect(second.deployments).toHaveLength(5);
		} finally {
			globalThis.fetch = original;
		}
		expect(calls).toEqual(['?limit=50&offset=0', '?limit=50&offset=50']);
	});

	it('fails closed when the server counts differ from the rows it sent', async () => {
		const original = globalThis.fetch;
		globalThis.fetch = (async () =>
			new Response(
				JSON.stringify({ deployments: [deployment()], limit: 50, offset: 0, returned: 7 }),
				{
					status: 200,
					headers: { 'content-type': 'application/json' }
				}
			)) as typeof fetch;
		try {
			await expect(listDeploymentsPage(50, 0)).rejects.toThrow(/inconsistent/);
		} finally {
			globalThis.fetch = original;
		}
	});

	it('rejects an inventory page larger than the requested limit', async () => {
		const original = globalThis.fetch;
		globalThis.fetch = (async () =>
			new Response(
				JSON.stringify({ deployments: [deployment(), deployment({ id: 'other' })], returned: 2 }),
				{ status: 200, headers: { 'content-type': 'application/json' } }
			)) as typeof fetch;
		try {
			await expect(listDeploymentsPage(1, 0)).rejects.toThrow(/exceeded the requested page limit/);
		} finally {
			globalThis.fetch = original;
		}
	});
});

describe('accepted messages', () => {
	it('uses instruction, not settlement, language', () => {
		expect(lifecycleAcceptedMessage('stop')).toContain('Managed stop accepted');
		expect(lifecycleAcceptedMessage('flatten')).toContain('Flatten request accepted');
		expect(lifecycleAcceptedMessage('pause')).toContain('New entries are blocked');
		expect(lifecycleAcceptedMessage('resume')).toContain('Entries may follow');
		expect(lifecycleAcceptedMessage('reset-breakers')).toContain('Breaker latches reset');
	});
});

describe('reset-breakers confirmation dialog', () => {
	it('names the latched breaker and does not bundle a lifecycle change', () => {
		const dialog = lifecycleDialog(
			deployment({ status: 'paused', daily_loss_latched: true }),
			'reset-breakers'
		);
		expect(dialog.title).toBe('Reset breaker latches?');
		expect(dialog.body.join('\n')).toContain('Daily-loss breaker latch is currently blocking');
		expect(dialog.body.join('\n')).toContain('does not change the lifecycle state');
		expect(dialog.body.join('\n')).toContain('Verify the cause before resetting');
		expect(dialog.confirm).toBe('Reset latches');
		expect(dialog.chooseStopMode).toBe(false);
		expect(dialog.danger).toBe(false);
	});

	it('pluralizes both latches', () => {
		const dialog = lifecycleDialog(
			deployment({ status: 'paused', daily_loss_latched: true, drawdown_latched: true }),
			'reset-breakers'
		);
		expect(dialog.body.join('\n')).toContain('latches are currently blocking');
	});
});

describe('operator performance provenance', () => {
	it('labels amounts with the provenance currency and never invents one', () => {
		expect(
			performanceReportText({
				mode: 'paper',
				currency: 'USDC',
				trade_count: 3,
				total_net_pnl: '12.5',
				total_return_fraction: '0.00125',
				mark_complete: true
			})
		).toContain('12.5 USDC');
		const unknown = performanceReportText({
			mode: 'paper',
			currency: null,
			trade_count: 1,
			total_net_pnl: '5',
			total_return_fraction: '0.0005',
			mark_complete: true
		});
		expect(unknown).toContain('unknown quote');
		expect(unknown).not.toContain('USD');
		expect(unknown).not.toContain('USDC');
	});

	it('marks incomplete marks as not final', () => {
		const text = performanceReportText({
			mode: 'live',
			currency: 'USD',
			trade_count: 2,
			total_net_pnl: '1',
			total_return_fraction: '0.0001',
			mark_complete: false
		});
		expect(text).toContain('open inventory without a mark — not final');
	});

	it('says when performance is not yet computable', () => {
		expect(
			performanceReportText({
				mode: 'paper',
				currency: 'USDC',
				trade_count: 0,
				total_net_pnl: null,
				total_return_fraction: null,
				mark_complete: false
			})
		).toBe('Net performance not yet computed from the fill ledger.');
	});

	it('caveats runtime drawdown but not backtest drawdown', () => {
		expect(drawdownIsCaveated({ mode: 'paper', maximum_drawdown_fraction: '0.05' })).toBe(true);
		expect(drawdownIsCaveated({ mode: 'backtest', maximum_drawdown_fraction: '0.05' })).toBe(false);
		expect(drawdownIsCaveated({ mode: 'paper', maximum_drawdown_fraction: null })).toBe(false);
	});

	it('renders an empty suffix for unknown currency', () => {
		expect(performanceCurrencySuffix('USD')).toBe(' USD');
		expect(performanceCurrencySuffix(null)).toBe('');
	});
});

describe('exact-version evidence links', () => {
	it('scopes backtests and research links to this exact fingerprint', () => {
		const current = deployment();
		const links = exactVersionEvidenceLinks(current);
		const fingerprint = current.strategy_fingerprint!;
		expect(links).toHaveLength(2);
		expect(links[0]!.label).toBe('Backtests of this version');
		expect(links[0]!.href).toBe(
			`/backtests?strategy_fingerprint=${encodeURIComponent(fingerprint)}`
		);
		expect(links[1]!.label).toBe('Research for this version');
		expect(links[1]!.href).toBe(
			`/research?strategy=strat-1&strategy_fingerprint=${encodeURIComponent(fingerprint)}`
		);
	});

	it('offers no version-scoped evidence for a discretionary deployment', () => {
		expect(exactVersionEvidenceLinks(deployment({ strategy_fingerprint: null }))).toEqual([]);
	});
});

describe('requested fingerprint resolution fails closed', () => {
	const versions = [
		{ strategy_fingerprint: 'sha256:' + 'a'.repeat(64) },
		{ strategy_fingerprint: 'sha256:' + 'b'.repeat(64) }
	];

	it('honors a matching explicit fingerprint', () => {
		const requested = 'sha256:' + 'a'.repeat(64);
		const resolution = resolveRequestedFingerprint(requested, versions);
		expect(resolution).toEqual({ selected: requested, blocked: false, notice: null });
	});

	it('defaults to latest only when no fingerprint was requested', () => {
		const resolution = resolveRequestedFingerprint('', versions);
		expect(resolution.selected).toBe('sha256:' + 'b'.repeat(64));
		expect(resolution.blocked).toBe(false);
	});

	it('blocks launch on an unknown explicit fingerprint instead of picking latest', () => {
		const resolution = resolveRequestedFingerprint('sha256:' + 'd'.repeat(64), versions);
		expect(resolution.selected).toBe('');
		expect(resolution.blocked).toBe(true);
		expect(resolution.notice).toContain('Launch is blocked');
		expect(resolution.notice).not.toContain('showing the latest');
	});

	it('blocks when nothing is published but a fingerprint was requested', () => {
		const resolution = resolveRequestedFingerprint('sha256:' + 'd'.repeat(64), []);
		expect(resolution.selected).toBe('');
		expect(resolution.blocked).toBe(true);
	});
});

describe('strategy config summary from the source API', () => {
	const draft = {
		schema_version: '1.0',
		strategy_id: 'strat-1',
		version: 2,
		name: 'UNI trend',
		description: null,
		status: 'published',
		created_at: '2026-09-01T00:00:00Z',
		instrument: { product_id: 'UNI-USDC', base_currency: 'UNI', quote_currency: 'USDC' },
		timeframe: '2h',
		data_requirements: {
			warmup_bars: 50,
			required_fields: ['open', 'high', 'low', 'close', 'volume']
		},
		indicators: [
			{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
			{ id: 'ema_slow', kind: 'ema', input: 'close', parameters: { period: 50 } }
		],
		entry: {
			side: 'long',
			when: {
				all: [
					{
						left: { indicator: 'ema_fast' },
						operator: 'crosses_above',
						right: { indicator: 'ema_slow' }
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
			initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr_14', multiple: '2' },
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
	} as Parameters<typeof summarizeStrategySource>[1];

	it('reads the immutable rule and config from the source definition', () => {
		const summary = summarizeStrategySource('sha256:' + 'a'.repeat(64), draft);
		expect(summary.rule).toContain('UNI trend');
		expect(summary.rule).toContain('UNI-USDC');
		expect(summary.entry).toContain('ema_fast crosses above ema_slow');
		expect(summary.indicators).toEqual([
			'ema_fast: ema(period=20) on close',
			'ema_slow: ema(period=50) on close'
		]);
		expect(summary.exits.some((line) => line.includes('atr_multiple'))).toBe(true);
		expect(summary.sizing.some((line) => line.includes('0.005'))).toBe(true);
		expect(summary.htfEntry).toBeNull();
	});

	it('surfaces the HTF filter when the definition has one', () => {
		const withHtf = {
			...draft,
			htf_filter: {
				timeframe: '1d',
				data_requirements: { warmup_bars: 30 },
				indicators: [{ id: 'd_ema', kind: 'ema', input: 'close', parameters: { period: 20 } }],
				when: {
					all: [{ left: { indicator: 'd_ema' }, operator: 'greater_than', right: { literal: '0' } }]
				}
			}
		} as Parameters<typeof summarizeStrategySource>[1];
		const summary = summarizeStrategySource('sha256:' + 'a'.repeat(64), withHtf);
		expect(summary.htfEntry).toContain('1d');
		expect(summary.htfEntry).toContain('warmup 30 bars');
	});
});

describe('complete inventory fetch', () => {
	it('follows hasMore across offset pages instead of taking the first page', async () => {
		const full = Array.from({ length: 200 }, (_, index) => deployment({ id: `dep-${index}` }));
		const second = full.slice(0, 7);
		const calls: string[] = [];
		const original = globalThis.fetch;
		globalThis.fetch = (async (input: RequestInfo | URL) => {
			const url = new URL(String(input), 'http://local');
			calls.push(url.search);
			const offset = Number(url.searchParams.get('offset'));
			const rows = offset === 0 ? full : second;
			return new Response(
				JSON.stringify({ deployments: rows, limit: 200, offset, returned: rows.length }),
				{ status: 200, headers: { 'content-type': 'application/json' } }
			);
		}) as typeof fetch;
		try {
			const rows = await listAllDeployments();
			expect(rows).toHaveLength(207);
		} finally {
			globalThis.fetch = original;
		}
		expect(calls).toEqual(['?limit=200&offset=0', '?limit=200&offset=200']);
	});

	it('fails closed at the page cap instead of returning a prefix', async () => {
		const full = Array.from({ length: 200 }, (_, index) => deployment({ id: `dep-${index}` }));
		const original = globalThis.fetch;
		globalThis.fetch = (async () =>
			new Response(JSON.stringify({ deployments: full, limit: 200, offset: 0, returned: 200 }), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			})) as typeof fetch;
		try {
			await expect(listAllDeployments()).rejects.toThrow(/truncated/);
		} finally {
			globalThis.fetch = original;
		}
	});
});
