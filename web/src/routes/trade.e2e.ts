import { expect, test } from '../e2e/harness';

test('trade ticket places a paper long through the discretionary HTTP contract', async ({
	page
}) => {
	await page.route('**/api/v1/deployments', async (route) => {
		await route.fulfill({ json: { deployments: [] } });
	});
	await page.route('**/api/v1/discretionary-orders', async (route) => {
		if (route.request().method() !== 'POST') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		const body = route.request().postDataJSON() as {
			origin?: string;
			mode?: string;
			stop_price?: string;
			take_profit_price?: string;
			idempotency_key?: string;
		};
		expect(body.origin).toBe('human');
		expect(body.mode).toBe('paper');
		expect(body.side ?? 'long').toBe('long');
		expect(body.stop_price).toBe('90000');
		expect(body.take_profit_price).toBe('120000');
		expect(body.idempotency_key).toBeTruthy();
		await route.fulfill({
			json: {
				id: '01985cf0-7b60-7000-8000-0000000000aa',
				strategy_fingerprint: null,
				strategy_id: null,
				kind: 'discretionary',
				timeframe: '5m',
				product_id: 'BTC-USD',
				mode: 'paper',
				status: 'running',
				phase: 'pending_entry',
				cash: '10000',
				paper_starting_cash: '10000',
				last_evaluated_bar: null,
				last_signal: 'discretionary',
				mismatch_detail: null,
				pending_entry_bars: 0,
				bars_held: 0,
				created_at: '2026-09-15T00:00:00+00:00',
				updated_at: '2026-09-15T00:00:00+00:00',
				position: null,
				orders: [],
				fills: []
			}
		});
	});

	await page.goto('/trade');
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Trade' })).toHaveAttribute('aria-current', 'page');
	const ticket = page.getByTestId('discretionary-ticket');
	await expect(ticket).toHaveAttribute('data-hydrated', 'true');
	await ticket.getByRole('textbox', { name: 'Limit price' }).fill('100000');
	await ticket.getByRole('textbox', { name: 'Quantity' }).fill('0.01');
	await ticket.getByRole('textbox', { name: 'Stop loss' }).fill('90000');
	await ticket.getByRole('textbox', { name: 'Take profit' }).fill('120000');
	await expect(ticket.getByRole('textbox', { name: 'Stop loss' })).toHaveValue('90000');
	await ticket.getByRole('button', { name: 'Place long' }).click();
	await expect(page.getByTestId('discretionary-result')).toContainText('pending_entry');
});
