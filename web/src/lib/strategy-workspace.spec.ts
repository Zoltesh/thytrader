import { describe, expect, it } from 'vitest';
import type { CoinbaseCredentialsStatus } from './credentials';
import type { Deployment } from './deployments';
import type { Portfolio } from './portfolio';
import type { StrategyLibraryEntry } from './strategies';
import {
	invalidStartReason,
	isStrategyFingerprint,
	latestSignalExplanation,
	libraryPipeline,
	livePreflight,
	paperEvidenceText,
	parseBuildSection,
	pipelineSummary,
	rulesLabel,
	rulesState,
	shortStrategyFingerprint,
	stageFromRouteId,
	timeframeMinutes,
	workspaceHref,
	type LivePreflightInput
} from './strategy-workspace';

const fpA = `sha256:${'a'.repeat(64)}`;
const fpB = `sha256:${'b'.repeat(64)}`;

function entry(overrides: Partial<StrategyLibraryEntry> = {}): StrategyLibraryEntry {
	return {
		strategy_id: 's-1',
		name: 'Trend',
		product_id: 'BTC-USDC',
		timeframe: '1h',
		revision: 1,
		valid: true,
		current_fingerprint: fpA,
		summary: '',
		backtest: null,
		paper_live: { paper: 'none', live: 'none' },
		active_deployment_count: 0,
		created_at: '2026-09-01T00:00:00Z',
		updated_at: '2026-09-01T00:00:00Z',
		...overrides
	};
}

describe('workspace routes', () => {
	it('builds stage URLs with a Test-only result and never a version', () => {
		expect(workspaceHref('s 1', 'build')).toBe('/strategies/s%201');
		expect(workspaceHref('s', 'test', { result: fpB })).toBe(
			`/strategies/s/test?result=${encodeURIComponent(fpB)}`
		);
		expect(workspaceHref('s', 'run', { result: fpB })).toBe('/strategies/s/run');
		expect(workspaceHref('s', 'why')).toBe('/strategies/s/why');
	});

	it('opens a builder section only on Build and rejects unknown sections', () => {
		expect(workspaceHref('s', 'build', { section: 'entry' })).toBe('/strategies/s?section=entry');
		expect(workspaceHref('s', 'test', { section: 'entry' })).toBe('/strategies/s/test');
		expect(parseBuildSection('indicators')).toBe('indicators');
		expect(parseBuildSection('nope')).toBeNull();
		expect(parseBuildSection(null)).toBeNull();
	});

	it('maps route ids to stages', () => {
		expect(stageFromRouteId('/strategies/[id]')).toBe('build');
		expect(stageFromRouteId('/strategies/[id]/run')).toBe('run');
		expect(stageFromRouteId('/strategies')).toBeNull();
	});

	it('recognizes old fingerprint deep links', () => {
		expect(isStrategyFingerprint(fpA)).toBe(true);
		expect(isStrategyFingerprint('01985cf0-7b60-7000-8000-000000000003')).toBe(false);
		expect(isStrategyFingerprint(null)).toBe(false);
	});
});

describe('current rules vs earlier edit', () => {
	it('compares a row snapshot with the current fingerprint', () => {
		expect(rulesState(fpA, fpA)).toBe('current');
		expect(rulesState(fpB, fpA)).toBe('earlier');
		expect(rulesLabel('current')).toBe('Current rules');
		expect(rulesLabel('earlier')).toBe('Earlier edit');
	});

	it('never calls a row current when either side is unknown', () => {
		expect(rulesState(fpA, null)).toBe('unknown');
		expect(rulesState(null, fpA)).toBe('unknown');
	});

	it('explains why starts are blocked while the definition is invalid', () => {
		expect(invalidStartReason(1)).toContain('1 problem.');
		expect(invalidStartReason(3)).toContain('3 problems');
		expect(invalidStartReason(0)).toContain('1 problem');
	});
});

describe('library pipeline', () => {
	it('shows Build as active while the saved definition has problems', () => {
		const steps = libraryPipeline(entry({ valid: false, current_fingerprint: null }));
		expect(steps.map((step) => step.state)).toEqual(['active', 'none', 'none', 'none']);
		expect(steps[0].detail).toBe('definition has problems');
	});

	it('derives Test from an attached backtest and Paper / Live from runtime status', () => {
		const steps = libraryPipeline(
			entry({
				backtest: {
					result_fingerprint: fpB,
					strategy_fingerprint: fpA,
					published_at: '2026-09-02T00:00:00Z',
					summary: {
						initial_equity: '1',
						final_equity: '1',
						total_return_fraction: '0',
						trade_count: 0,
						win_rate: '0',
						maximum_drawdown_fraction: '0'
					}
				},
				paper_live: { paper: 'running', live: 'stopped' }
			})
		);
		expect(steps.map((step) => [step.stage, step.state, step.detail])).toEqual([
			['Build', 'done', 'rules valid'],
			['Test', 'done', 'has a backtest'],
			['Paper', 'paper', 'running'],
			['Live', 'done', 'stopped']
		]);
		expect(pipelineSummary(steps)).toBe(
			'Build: rules valid; Test: has a backtest; Paper: running; Live: stopped'
		);
	});

	it('marks live running with the live state and never shows version pills', () => {
		const steps = libraryPipeline(entry({ paper_live: { paper: 'paused', live: 'running' } }));
		expect(steps.map((step) => step.stage)).toEqual(['Build', 'Test', 'Paper', 'Live']);
		expect(steps[2]).toEqual({ stage: 'Paper', state: 'paper', detail: 'paused' });
		expect(steps[3]).toEqual({ stage: 'Live', state: 'live', detail: 'running' });
		expect(pipelineSummary(steps)).not.toMatch(/\bv\d/);
	});
});

const credentials = (configured: boolean): CoinbaseCredentialsStatus => ({
	provider: 'coinbase',
	configured,
	persisted: configured,
	env_file_writable: true,
	api_hot_reloaded: true,
	workers_require_restart: false,
	workers_restart_detail: ''
});

const portfolio = (overrides: Partial<Portfolio> = {}): Portfolio => ({
	as_of: '2026-09-29T12:00:00Z',
	connection: { provider: 'coinbase', status: 'connected', permissions: ['view', 'trade'] },
	demo: false,
	total_value: { amount: '1240.18', currency: 'USD' },
	assets: [
		{
			currency: 'USDC',
			name: 'USD Coin',
			available: '1240.18',
			hold: '0',
			total: '1240.18',
			value: null
		}
	],
	unvalued_assets: [],
	...overrides
});

function input(overrides: Partial<LivePreflightInput> = {}): LivePreflightInput {
	return {
		strategyId: 's-1',
		productId: 'BTC-USDC',
		timeframe: '1h',
		credentials: credentials(true),
		riskPolicy: {
			source: 'published',
			version: 7,
			quote_currency: 'USDC',
			allocations: [{ strategy_id: 's-1', allocated_quote: '100' }]
		},
		portfolio: portfolio(),
		userOrderFeed: null,
		...overrides
	};
}

describe('live preflight', () => {
	it('reports each existing source as an independent fact', () => {
		const items = livePreflight(input());
		expect(items.map((item) => [item.id, item.state])).toEqual([
			['credentials', 'ok'],
			['risk-policy', 'ok'],
			['balance', 'ok'],
			['allocation', 'ok'],
			['clock-feed', 'ok']
		]);
		expect(items[1].label).toBe('Risk policy published · v7');
		expect(items[2].label).toContain('1240.18 USDC available');
		expect(items[3].label).toBe('Allocation for this strategy: 100 USDC');
		expect(items[4].label).toContain('1h clock');
	});

	it('shows Unknown for every source that cannot be read, never a pass', () => {
		const items = livePreflight(
			input({
				timeframe: '5m',
				credentials: { unreadable: true },
				riskPolicy: { unreadable: true },
				portfolio: { unreadable: true },
				userOrderFeed: { unreadable: true }
			})
		);
		expect(items.every((item) => item.state === 'unknown')).toBe(true);
		expect(items.every((item) => item.label.includes('Unknown'))).toBe(true);
	});

	it('treats a demo portfolio as Unknown balance, not a Coinbase balance', () => {
		const [, , balance] = livePreflight(
			input({
				portfolio: portfolio({
					demo: true,
					connection: { provider: 'coinbase', status: 'demo', permissions: [] }
				})
			})
		);
		expect(balance.state).toBe('unknown');
		expect(balance.label).toContain('demo');
	});

	it('flags missing credentials, a compiled-default policy, no allocation, and a zero or absent quote balance', () => {
		const items = livePreflight(
			input({
				credentials: credentials(false),
				riskPolicy: {
					source: 'compiled_default',
					version: 0,
					quote_currency: 'USDC',
					allocations: []
				},
				portfolio: portfolio({ assets: [] })
			})
		);
		expect(items.slice(0, 4).map((item) => item.state)).toEqual([
			'attention',
			'attention',
			'attention',
			'attention'
		]);
		const zero = livePreflight(
			input({
				portfolio: portfolio({
					assets: [
						{ currency: 'USDC', name: 'USDC', available: '0', hold: '0', total: '0', value: null }
					]
				})
			})
		);
		expect(zero[2].state).toBe('attention');
	});

	it('uses the product quote, not a USD substitute', () => {
		const [, , balance] = livePreflight(input({ productId: 'BTC-USD' }));
		expect(balance.state).toBe('attention');
		expect(balance.label).toContain('No USD balance');
	});

	it('requires a connected user-order feed only for sub-hour clocks', () => {
		expect(
			livePreflight(input({ timeframe: '5m', userOrderFeed: { state: 'connected' } }))[4].state
		).toBe('ok');
		const stale = livePreflight(input({ timeframe: '15m', userOrderFeed: { state: 'stale' } }))[4];
		expect(stale.state).toBe('attention');
		expect(stale.label).toContain('stale');
		expect(livePreflight(input({ timeframe: '5m', userOrderFeed: null }))[4].state).toBe('unknown');
	});
});

describe('workspace formatting helpers', () => {
	it('shortens fingerprints without losing the algorithm', () => {
		expect(shortStrategyFingerprint(`sha256:9f3a${'0'.repeat(56)}c21e`)).toBe('sha256:9f3a…c21e');
		expect(shortStrategyFingerprint('short')).toBe('short');
	});

	it('parses venue clocks', () => {
		expect(timeframeMinutes('5m')).toBe(5);
		expect(timeframeMinutes('2h')).toBe(120);
		expect(timeframeMinutes('1d')).toBe(1440);
		expect(timeframeMinutes('weekly')).toBeNull();
	});

	it('keeps paper evidence informational', () => {
		expect(paperEvidenceText([])).toContain("Paper results don't qualify a strategy for live");
		const deployments = [
			{ ledger: { trade_count: 6 } },
			{ ledger: null }
		] as unknown as Deployment[];
		expect(paperEvidenceText(deployments)).toMatch(
			/^Paper: 2 deployments of this strategy, 6 closed trades\./
		);
	});
});

describe('latest completed-bar signal', () => {
	const base = { last_evaluated_bar: '2026-09-29T12:00:00Z', timeframe: '2h' } as Deployment;

	it('distinguishes did-not-match, could-not-evaluate, matched, and not evaluated', () => {
		expect(latestSignalExplanation({ ...base, last_signal: 'not_matched' }).title).toBe(
			'No trade — conditions did not match'
		);
		expect(latestSignalExplanation({ ...base, last_signal: 'not_matched' }).detail).toContain(
			'completed 2h bar at 2026-09-29T12:00:00Z'
		);
		expect(latestSignalExplanation({ ...base, last_signal: 'undefined' }).title).toBe(
			'No trade — conditions could not be evaluated'
		);
		expect(latestSignalExplanation({ ...base, last_signal: 'matched' }).kind).toBe('matched');
		expect(
			latestSignalExplanation({ ...base, last_evaluated_bar: null, last_signal: null }).kind
		).toBe('not-evaluated');
	});
});
