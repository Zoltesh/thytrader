import { type Page } from '@playwright/test';

import { expect, isStrategyLibraryRequest, test } from '../e2e/harness';

/**
 * Inventory adoption in the browser (ADR 0124): Trade "Adopt holdings", and the Home
 * holdings actions "Sell to USDC" and "Adopt into bot". Every live action sends
 * `i_understand_live: true` only after the dialog's checkbox is ticked.
 */

const BOOK = {
	strategy_fingerprint: null,
	strategy_id: null,
	kind: 'discretionary',
	timeframe: '5m',
	mode: 'live',
	phase: 'open',
	cash: '0',
	paper_starting_cash: null,
	last_evaluated_bar: null,
	last_signal: 'inventory_adoption',
	mismatch_detail: null,
	pending_entry_bars: 0,
	bars_held: 0,
	created_at: '2026-10-09T14:00:00+00:00',
	updated_at: '2026-10-09T14:00:00+00:00',
	position: null,
	orders: [],
	fills: []
};

function preview(productId: string, overrides: Record<string, unknown> = {}): unknown {
	return {
		product_id: productId,
		base_currency: productId.split('-')[0],
		timeframe: '5m',
		mark: '0.2',
		mark_bar_starts_at: '2026-10-09T14:00:00+00:00',
		base_increment: '1',
		balance_total: '150',
		balance_available: '150',
		claims: {
			managed_long: '100',
			working_buys: '0',
			working_short_entry_sells: '0',
			claimed: '100'
		},
		unmanaged: '50',
		adoptable: '50',
		unresolved_reasons: [],
		protect_blocking_reasons: [],
		sell_blocking_reasons: [],
		...overrides
	};
}

async function mockPreview(page: Page, overrides: Record<string, unknown> = {}): Promise<void> {
	await page.route('**/api/v1/inventory-adoptions/preview**', (route) => {
		const product = new URL(route.request().url()).searchParams.get('product_id') ?? '';
		return route.fulfill({ json: preview(product, overrides) });
	});
}

test('Trade "Adopt holdings" shows unmanaged coins and protects them after the live checkbox', async ({
	page
}) => {
	await page.route('**/api/v1/memory/trade-reasons**', (route) =>
		route.fulfill({ json: { trade_reasons: [] } })
	);
	await mockPreview(page);
	const posted: Record<string, unknown>[] = [];
	await page.route('**/api/v1/inventory-adoptions', async (route) => {
		posted.push(route.request().postDataJSON() as Record<string, unknown>);
		await route.fulfill({
			status: 201,
			json: { ...BOOK, id: '01985cf0-7b60-7000-8000-0000000000c1', product_id: 'DOGE-USDC' }
		});
	});
	await page.goto('/trade');
	const ticket = page.getByTestId('discretionary-ticket');
	await expect(ticket).toHaveAttribute('data-hydrated', 'true');
	await ticket.getByRole('textbox', { name: 'Product' }).fill('DOGE-USDC');
	await ticket
		.getByTestId('discretionary-entry')
		.getByRole('button', { name: 'Adopt holdings' })
		.click();
	const adopt = page.getByTestId('trade-adopt');
	await expect(adopt.getByTestId('adoption-unmanaged')).toHaveText('50');
	await expect(adopt.getByTestId('adoption-managed')).toHaveText('100');
	await expect(page.getByTestId('trade-review')).toHaveCount(0);
	await adopt.getByTestId('adopt-stop').fill('0.15');
	await adopt.getByTestId('adopt-target').fill('0.3');

	// Paper refuses before any request: adoption is live only.
	await adopt.getByTestId('adopt-review').click();
	await expect(adopt.getByRole('alert')).toContainText('live only');
	expect(posted).toHaveLength(0);

	await ticket.getByTestId('trade-mode').getByRole('button', { name: 'Live' }).click();
	await adopt.getByTestId('adopt-review').click();
	const dialog = page.getByTestId('adopt-dialog');
	await expect(dialog).toBeVisible();
	const confirm = dialog.getByRole('button', { name: 'Adopt and protect' });
	await expect(confirm).toBeDisabled();
	await dialog.getByTestId('adopt-ack').check();
	await confirm.click();
	await expect(dialog).toBeHidden();
	expect(posted).toHaveLength(1);
	expect(posted[0]).toMatchObject({
		mode: 'live',
		action: 'protect',
		product_id: 'DOGE-USDC',
		quantity: 'all',
		stop_price: '0.15',
		take_profit_price: '0.3',
		origin: 'human',
		i_understand_live: true
	});
	expect(posted[0].idempotency_key).toBeTruthy();
});

test('Trade "Adopt holdings" cannot be reviewed while the preview blocks it', async ({ page }) => {
	await mockPreview(page, {
		protect_blocking_reasons: ['ADOPTION_BOOK_OCCUPIED: book 1 holds DOGE-USDC.']
	});
	await page.goto('/trade');
	const ticket = page.getByTestId('discretionary-ticket');
	await expect(ticket).toHaveAttribute('data-hydrated', 'true');
	await ticket.getByRole('textbox', { name: 'Product' }).fill('DOGE-USDC');
	await ticket
		.getByTestId('discretionary-entry')
		.getByRole('button', { name: 'Adopt holdings' })
		.click();
	const adopt = page.getByTestId('trade-adopt');
	await expect(adopt.getByTestId('adoption-blocking')).toContainText('ADOPTION_BOOK_OCCUPIED');
	await expect(adopt.getByTestId('adopt-review')).toBeDisabled();
});

const connectedPortfolio = {
	as_of: new Date().toISOString(),
	connection: { provider: 'coinbase', status: 'connected', permissions: ['view', 'trade'] },
	demo: false,
	total_value: { amount: '530.00', currency: 'USD' },
	assets: [
		{
			currency: 'USDC',
			name: 'USD Coin',
			available: '500.00',
			hold: '0.00',
			total: '500.00',
			value: { amount: '500.00', currency: 'USD' }
		},
		{
			currency: 'DOGE',
			name: 'Dogecoin',
			available: '150',
			hold: '0',
			total: '150',
			value: { amount: '30.00', currency: 'USD' }
		}
	],
	unvalued_assets: []
};

async function openHoldings(page: Page): Promise<void> {
	await page.route('**/api/v1/portfolio', (route) => route.fulfill({ json: connectedPortfolio }));
	await mockPreview(page);
	await page.goto('/');
	await expect(page.getByTestId('holdings').getByTestId('holding-row')).toHaveCount(2);
}

test('Holdings "Sell to USDC" sends a live sell only after the checkbox', async ({ page }) => {
	const posted: Record<string, unknown>[] = [];
	await page.route('**/api/v1/inventory-adoptions', async (route) => {
		posted.push(route.request().postDataJSON() as Record<string, unknown>);
		await route.fulfill({
			status: 201,
			json: {
				...BOOK,
				id: '01985cf0-7b60-7000-8000-0000000000c2',
				product_id: 'DOGE-USDC',
				status: 'stopped'
			}
		});
	});
	await openHoldings(page);
	const holdings = page.getByTestId('holdings');
	// Cash rows have no actions; coin rows do.
	const usdc = holdings.getByTestId('holding-row').filter({ hasText: 'USD Coin' });
	await expect(usdc.getByRole('button', { name: 'Sell to USDC' })).toHaveCount(0);
	const doge = holdings.getByTestId('holding-row').filter({ hasText: 'Dogecoin' });
	await doge.getByRole('button', { name: 'Sell to USDC' }).click();

	const dialog = page.getByTestId('holding-action-dialog');
	await expect(dialog.getByTestId('adoption-unmanaged')).toHaveText('50');
	const confirm = dialog.getByRole('button', { name: 'Sell to USDC' });
	await expect(confirm).toBeDisabled();
	await dialog.getByTestId('holding-ack').check();
	await confirm.click();
	await expect(page.getByTestId('holding-action-done')).toContainText('Sell book created');
	expect(posted).toHaveLength(1);
	expect(posted[0]).toMatchObject({
		mode: 'live',
		action: 'sell',
		product_id: 'DOGE-USDC',
		quantity: 'all',
		origin: 'human',
		i_understand_live: true
	});
});

test('Holdings "Adopt into bot" starts the chosen strategy live with adopt_holdings', async ({
	page
}) => {
	await page.route(isStrategyLibraryRequest, (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy_id: '11111111-1111-4111-8111-111111111111',
						name: 'DOGE trend',
						product_id: 'DOGE-USDC',
						timeframe: '1h',
						revision: 1,
						valid: true,
						current_fingerprint: null,
						summary: null,
						created_at: '2026-10-01T00:00:00Z',
						updated_at: '2026-10-01T00:00:00Z',
						backtest: null,
						paper_live: { paper: 'none', live: 'none' },
						active_deployment_count: 0
					}
				],
				has_more: false,
				next_cursor: null
			}
		})
	);
	const started: Record<string, unknown>[] = [];
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		async (route) => {
			if (route.request().method() !== 'POST') {
				await route.fallback();
				return;
			}
			started.push(route.request().postDataJSON() as Record<string, unknown>);
			await route.fulfill({
				status: 201,
				json: {
					...BOOK,
					id: '01985cf0-7b60-7000-8000-0000000000c3',
					kind: 'strategy',
					product_id: 'DOGE-USDC'
				}
			});
		}
	);
	await openHoldings(page);
	const doge = page
		.getByTestId('holdings')
		.getByTestId('holding-row')
		.filter({ hasText: 'Dogecoin' });
	await doge.getByRole('button', { name: 'Adopt into bot' }).click();
	const dialog = page.getByTestId('holding-action-dialog');
	await expect(dialog.getByTestId('holding-strategy')).toHaveValue(
		'11111111-1111-4111-8111-111111111111'
	);
	await dialog.getByTestId('holding-all').uncheck();
	await dialog.getByTestId('holding-quantity').fill('40');
	await dialog.getByTestId('holding-ack').check();
	await dialog.getByRole('button', { name: 'Start live bot' }).click();
	await expect(page.getByTestId('holding-action-done')).toContainText('Live bot started');
	expect(started).toEqual([
		{
			strategy_id: '11111111-1111-4111-8111-111111111111',
			mode: 'live',
			adopt_holdings: '40',
			i_understand_live: true
		}
	]);
});
