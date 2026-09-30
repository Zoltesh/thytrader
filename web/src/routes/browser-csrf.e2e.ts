import { expect, isStrategyLibraryRequest, test } from '../e2e/harness';

test('new strategy passes CSRF and persists when storage is configured', async ({ page }) => {
	const status = await page.request.get('/api/v1/security/status');
	expect(status.ok()).toBe(true);
	const security = await status.json();
	expect(security.enabled).toBe(true);
	expect(security.csrf_required_for_browser).toBe(true);

	const sessions: number[] = [];
	page.on('response', (response) => {
		if (new URL(response.url()).pathname === '/api/v1/security/session') {
			sessions.push(response.status());
		}
	});
	const listed = page.waitForResponse(
		(response) =>
			isStrategyLibraryRequest(new URL(response.url())) && response.request().method() === 'GET'
	);
	await page.goto('/strategies');
	await listed;
	if (process.env.THYTRADER_E2E_DATABASE_URL) {
		await expect(page.getByRole('alert')).toHaveCount(0);
	} else {
		await expect(page.getByRole('alert')).toContainText('Strategy storage is unavailable');
	}
	const created = page.waitForResponse((response) => response.request().method() === 'POST');
	await page.getByRole('button', { name: 'New strategy' }).click();
	const response = await created;
	expect(sessions).toContain(200);
	const headers = await response.request().allHeaders();
	expect(Boolean(headers['x-csrf-token'])).toBe(true);
	expect(Boolean(headers['cookie'])).toBe(true);
	if (!process.env.THYTRADER_E2E_DATABASE_URL) {
		expect(response.status()).toBe(503);
		const detail = ((await response.json()) as { detail: string | { message: string } }).detail;
		expect(typeof detail === 'string' ? detail : detail.message).toBe(
			'Strategy storage is unavailable.'
		);
		return;
	}

	expect(response.status()).toBe(201);
	const created_ = (await response.json()) as { strategy_id: string };
	const library = await page.request.get('/api/v1/strategies?limit=10');
	expect(library.ok()).toBe(true);
	expect((await library.json()).strategies).toEqual(
		expect.arrayContaining([expect.objectContaining({ strategy_id: created_.strategy_id })])
	);
});
