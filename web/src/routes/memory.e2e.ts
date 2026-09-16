import { expect, test } from '../e2e/harness';

const TRADE_REASON = {
	schema_version: 'thytrader-trade-reason-v1',
	id: '01985cf0-7b60-7000-8000-000000000021',
	created_at: '2026-09-16T12:00:00Z',
	origin: 'human',
	intent_id: '01985cf0-7b60-7000-8000-000000000022',
	deployment_id: '01985cf0-7b60-7000-8000-0000000000aa',
	deployment_kind: 'discretionary',
	mode: 'paper',
	product_id: 'BTC-USD',
	purpose: 'entry',
	side: 'buy',
	strategy: null,
	signal: {
		kind: 'discretionary',
		last_signal: 'discretionary',
		candle_starts_at: '2026-09-16T12:00:00Z',
		timeframe: '5m'
	},
	risk: {
		decision: 'allow',
		reason_code: 'ALLOWED',
		detail: 'Admitted after risk.',
		policy_fingerprint: `sha256:${'b'.repeat(64)}`,
		policy_source: 'compiled_default'
	},
	notes: [
		{
			origin: 'human',
			body: 'Manual fade of the open.',
			recorded_at: '2026-09-16T12:00:00Z'
		}
	],
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

test('renders memory status and journals without mutation controls', async ({ page }) => {
	await page.route('**/api/v1/memory**', async (route) => {
		const url = new URL(route.request().url());
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'read-only' } });
			return;
		}
		if (url.pathname === '/api/v1/memory') {
			await route.fulfill({
				json: {
					schema_version: 'thytrader-experiential-memory-v1',
					counts: { journals: 1, sentiment: 0, patterns: 0, notifications: 0, trade_reasons: 1 },
					notify_provider: 'none',
					notify_webhook_configured: false,
					notify_enabled: false,
					storage: 'available'
				}
			});
			return;
		}
		if (url.pathname === '/api/v1/memory/monitor') {
			await route.fulfill({
				json: {
					schema_version: 'thytrader-monitor-v1',
					memory: {
						schema_version: 'thytrader-experiential-memory-v1',
						counts: {
							journals: 1,
							sentiment: 0,
							patterns: 0,
							notifications: 0,
							trade_reasons: 1
						},
						notify_provider: 'none',
						notify_webhook_configured: false,
						notify_enabled: false,
						storage: 'available'
					},
					deployments: [],
					recent_journals: [],
					recent_notifications: [],
					recent_trade_reasons: [TRADE_REASON],
					findings: []
				}
			});
			return;
		}
		if (url.pathname === '/api/v1/memory/journals') {
			await route.fulfill({
				json: {
					journals: [
						{
							id: '01985cf0-7b60-7000-8000-000000000011',
							occurred_at: '2026-09-15T12:00:00Z',
							origin: 'human',
							kind: 'note',
							title: 'Paper pause was correct',
							body: 'Paused after stale candles.',
							product_id: 'BTC-USD',
							runtime_mode: 'paper',
							lesson_outcome: 'none'
						}
					]
				}
			});
			return;
		}
		if (url.pathname === '/api/v1/memory/trade-reasons') {
			await route.fulfill({
				json: { schema_version: 'thytrader-trade-reason-v1', trade_reasons: [TRADE_REASON] }
			});
			return;
		}
		await route.fulfill({ json: { sentiment: [], patterns: [], notifications: [], journals: [] } });
	});

	await page.goto('/memory');
	await expect(page.getByRole('heading', { name: 'Journals and monitor' })).toBeVisible();
	await expect(page.getByTestId('memory-status')).toContainText('none');
	await expect(
		page.getByRole('cell', { name: 'Paper pause was correct', exact: true })
	).toBeVisible();
	await expect(page.getByTestId('trade-reason-review')).toContainText('discretionary');
	await expect(page.getByTestId('trade-reason-review')).toContainText('ALLOWED');
	await expect(page.getByTestId('trade-reason-review')).toContainText('Manual fade of the open.');
	await expect(page.getByRole('button', { name: /journal/i })).toHaveCount(0);
	await expect(page.getByRole('button', { name: /note/i })).toHaveCount(0);
});
