/** Pending requests cannot erase unresolved inventory evidence; no real venue calls. */
import { test, expect } from '../e2e/harness';
import { book } from '../e2e/futures-retry-fixtures';

for (const shown of [false, true]) {
	for (const initial of ['empty-failure', 'partial', 'last-good']) {
		test(`pending futures retry retains ${initial}, account ${shown}`, async ({ page }) => {
			const empty = {
				report_kind: 'futures_books',
				overall_status: 'healthy',
				components: [],
				payload: {
					paper_capital_usd: null,
					committed_paper_cash_usd: '0',
					futures_policy_set: false,
					books: [],
					collateral_note: 'USD is not added to USDC.',
					live_supported: true
				}
			};
			const good = {
				...empty,
				payload: { ...empty.payload, books: [book({ strategy_name: 'Synthetic retained book' })] }
			};
			const failure = {
				...empty,
				overall_status: 'failed',
				components: [
					{
						name: 'futures_books',
						status: 'failed',
						reason_code: 'STORE_UNAVAILABLE',
						detail: 'Synthetic unavailable'
					}
				],
				payload: { ...empty.payload, books: initial === 'partial' ? good.payload.books : [] }
			};
			let phase = initial === 'last-good' ? 'good' : 'failure';
			let retryStarted = false;
			let release!: () => void;
			const held = new Promise<void>((resolve) => {
				release = resolve;
			});
			await page.route('**/api/v1/operator/futures-account', (route) =>
				route.fulfill({
					json: {
						overall_status: shown ? 'healthy' : 'degraded',
						components: shown
							? []
							: [
									{
										name: 'futures_account',
										status: 'degraded',
										reason_code: 'FUTURES_MIRROR_NOT_RUN',
										detail: 'Not observed'
									}
								],
						payload: {
							observed_at: shown ? '2026-01-01T00:00:00Z' : null,
							age_seconds: null,
							stale: false,
							enablement: shown ? 'enabled' : null,
							read_failures: [],
							balance: null,
							positions: []
						}
					}
				})
			);
			await page.route('**/api/v1/operator/futures-books', async (route) => {
				if (phase === 'retry') {
					retryStarted = true;
					await held;
					return route.fulfill({ json: empty });
				}
				return route.fulfill({ json: phase === 'good' ? good : failure });
			});
			await page.goto('/');
			const card = page.getByTestId('futures-card');
			if (initial === 'last-good') {
				await expect(card).toContainText('Synthetic retained book');
				phase = 'failure';
				await card.getByRole('button', { name: 'Refresh futures books' }).click();
			}
			await expect(card).toContainText('Managed inventory is unknown');
			phase = 'retry';
			await card.getByRole('button', { name: 'Refresh futures books' }).click();
			await expect.poll(() => retryStarted).toBe(true);
			try {
				await expect(card).toBeVisible();
				await expect(card).toContainText('Managed inventory is unknown');
				if (initial !== 'empty-failure') {
					await expect(card).toContainText('Synthetic retained book');
					await expect(card).toContainText('stale or incomplete');
				}
				await expect(page.getByTestId('your-bots')).toBeVisible();
			} finally {
				release();
			}
			if (shown) {
				await expect(card).not.toContainText('Managed inventory is unknown');
			} else {
				await expect(card).toHaveCount(0);
			}
		});
	}
}
