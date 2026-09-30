import type { Page } from '@playwright/test';
import { expect, isStrategyLibraryRequest, test } from '../e2e/harness';

const chatStatus = {
	schema_version: 'thytrader-operator-chat-v1',
	llm_configured: false,
	provider: null,
	model: null,
	base_url: null,
	key_storage: 'api_process',
	coinbase_credentials_in_chat: false
};

/** Navigate and wait until the shell's client handlers are attached. */
async function gotoShell(page: Page, path: string): Promise<void> {
	await page.goto(path);
	await expect(page.locator('[data-shell-hydrated="true"]')).toHaveCount(1);
}

async function mockHome(page: Page): Promise<void> {
	await page.route('**/api/v1/portfolio', async (route) => {
		await route.fulfill({
			json: {
				as_of: '2026-07-27T22:15:00Z',
				connection: { provider: 'coinbase', status: 'demo', permissions: ['view'] },
				demo: true,
				total_value: { amount: '1', currency: 'USD' },
				assets: [],
				unvalued_assets: []
			}
		});
	});
	await page.route(isStrategyLibraryRequest, async (route) => {
		await route.fulfill({ json: { strategies: [] } });
	});
	await page.route('**/api/v1/operator-chat/transcript', async (route) => {
		await route.fulfill({
			json: {
				schema_version: 'thytrader-operator-chat-v1',
				status: chatStatus,
				messages: [],
				pending_confirmations: []
			}
		});
	});
}

test('rail shows four destinations, a System group, and marks the current page', async ({
	page
}) => {
	await mockHome(page);
	await gotoShell(page, '/');

	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav).toHaveCount(1);
	const primary = [
		['Home', '/'],
		['Strategies', '/strategies'],
		['Portfolio', '/deployments'],
		['Trade', '/trade']
	] as const;
	for (const [label, href] of primary) {
		await expect(nav.getByRole('link', { name: label, exact: true })).toHaveAttribute('href', href);
	}
	await expect(nav.getByRole('link', { name: 'Home', exact: true })).toHaveAttribute(
		'aria-current',
		'page'
	);
	// Chat is the Agent panel now, not a rail destination; the old context pill is gone.
	await expect(nav.getByRole('link', { name: 'Chat' })).toHaveCount(0);
	await expect(page.getByText('Local workstation')).toHaveCount(0);
	await expect(page.getByTestId('breadcrumb')).toHaveText('Home');

	const system = nav.getByRole('button', { name: 'System' });
	await expect(system).toHaveAttribute('aria-expanded', 'false');
	await expect(nav.getByRole('link', { name: 'Settings' })).toHaveCount(0);
	await system.click();
	await expect(system).toHaveAttribute('aria-expanded', 'true');
	for (const [label, href] of [
		['Settings', '/settings'],
		['Audit log', '/audit'],
		['Journal', '/journals'],
		['Memory & why-trade', '/memory']
	] as const) {
		await expect(nav.getByRole('link', { name: label, exact: true })).toHaveAttribute('href', href);
	}

	await nav.getByRole('link', { name: 'Strategies', exact: true }).click();
	await expect(page).toHaveURL(/\/strategies\/?$/);
	await expect(nav.getByRole('link', { name: 'Strategies', exact: true })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(nav.getByRole('link', { name: 'Home', exact: true })).not.toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(page.getByTestId('breadcrumb')).toHaveText(/Strategies\s*\/\s*Library/);
});

test('rail stays reachable on a narrow desktop', async ({ page }) => {
	await mockHome(page);
	await page.setViewportSize({ width: 700, height: 800 });
	await gotoShell(page, '/');
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	for (const label of ['Home', 'Strategies', 'Portfolio', 'Trade']) {
		await expect(nav.getByRole('link', { name: label, exact: true })).toBeVisible();
	}
	await nav.getByRole('link', { name: 'Portfolio', exact: true }).click();
	await expect(page).toHaveURL(/\/deployments\/?$/);
	await expect(nav.getByRole('link', { name: 'Portfolio', exact: true })).toHaveAttribute(
		'aria-current',
		'page'
	);
});

test('strategy builder keeps Strategies active', async ({ page }) => {
	await page.route('**/api/v1/strategies**', async (route) => {
		await route.fulfill({ json: { strategies: [] } });
	});
	await page.goto('/strategies/01985cf0-7b60-7000-8000-000000000007');

	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Strategies', exact: true })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(page.getByTestId('breadcrumb')).toHaveText(/Strategies\s*\/\s*Build/);
});

test('command palette opens with Ctrl+K, navigates by keyboard, and restores focus', async ({
	page
}) => {
	await mockHome(page);
	await gotoShell(page, '/');
	const trigger = page.getByRole('button', { name: 'Search pages and actions' });
	await expect(trigger).toBeVisible();

	// Typing in a field is never swallowed by the global shortcut handler.
	await page.getByTestId('theme-toggle').focus();
	await page.keyboard.press('Control+k');
	const palette = page.getByRole('dialog', { name: 'Command palette' });
	await expect(palette).toBeVisible();
	const search = palette.getByRole('combobox', { name: 'Search pages and actions' });
	await expect(search).toBeFocused();
	for (const label of [
		'Home',
		'Strategies',
		'Portfolio',
		'Trade',
		'Settings',
		'Audit log',
		'Journal',
		'Memory & why-trade',
		'Open agent',
		'New strategy'
	]) {
		// Option names are the label plus an optional hint ("Open agent Side panel").
		const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
		await expect(
			palette.getByRole('option', { name: new RegExp(`^${escaped}(\\s|$)`) })
		).toHaveCount(1);
	}

	await search.fill('aud');
	await expect(palette.getByRole('option')).toHaveCount(1);
	await expect(palette.getByRole('option', { name: 'Audit log' })).toHaveAttribute(
		'aria-selected',
		'true'
	);
	await search.fill('');
	await page.keyboard.press('ArrowDown');
	await expect(palette.getByRole('option', { name: 'Strategies', exact: true })).toHaveAttribute(
		'aria-selected',
		'true'
	);
	await page.keyboard.press('ArrowUp');
	await expect(palette.getByRole('option', { name: 'Home', exact: true })).toHaveAttribute(
		'aria-selected',
		'true'
	);

	await page.keyboard.press('Escape');
	await expect(palette).toBeHidden();
	await expect(page.getByTestId('theme-toggle')).toBeFocused();

	await trigger.click();
	await expect(palette).toBeVisible();
	await search.fill('trade');
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/trade\/?$/);
	await expect(palette).toBeHidden();
});

test('command palette shortcut does not hijack typing in inputs', async ({ page }) => {
	await mockHome(page);
	await gotoShell(page, '/');
	await page.getByRole('button', { name: 'Search pages and actions' }).click();
	const palette = page.getByRole('dialog', { name: 'Command palette' });
	const search = palette.getByRole('combobox', { name: 'Search pages and actions' });
	await search.pressSequentially('kkk');
	await expect(search).toHaveValue('kkk');
	await expect(palette).toBeVisible();
});

test('agent panel toggles on every page, shows context, and persists per viewer', async ({
	page
}) => {
	await mockHome(page);
	await gotoShell(page, '/');
	const toggle = page.getByRole('button', { name: 'Agent', exact: true });
	await expect(toggle).toHaveAttribute('aria-pressed', 'false');
	await expect(page.getByRole('complementary', { name: 'Agent' })).toHaveCount(0);

	await toggle.click();
	const panel = page.getByRole('complementary', { name: 'Agent' });
	await expect(panel).toBeVisible();
	await expect(toggle).toHaveAttribute('aria-pressed', 'true');
	await expect(panel.getByTestId('agent-context')).toHaveText('Looking at: Home');
	await expect(panel.getByTestId('llm-key-panel')).toBeVisible();
	await expect(panel.getByTestId('llm-api-key')).toHaveAttribute('type', 'password');
	await expect(panel.getByTestId('chat-draft')).toBeDisabled();

	await page
		.getByRole('navigation', { name: 'Primary navigation' })
		.getByRole('link', { name: 'Trade', exact: true })
		.click();
	await expect(panel.getByTestId('agent-context')).toHaveText('Looking at: Trade');

	await page.reload();
	await expect(page.locator('[data-shell-hydrated="true"]')).toHaveCount(1);
	await expect(page.getByRole('complementary', { name: 'Agent' })).toBeVisible();

	await page.getByRole('button', { name: 'Close agent' }).click();
	await expect(page.getByRole('complementary', { name: 'Agent' })).toHaveCount(0);
	await expect(toggle).toBeFocused();
	await page.reload();
	await expect(page.locator('[data-shell-hydrated="true"]')).toHaveCount(1);
	await expect(page.getByRole('button', { name: 'Agent', exact: true })).toHaveAttribute(
		'aria-pressed',
		'false'
	);
});

test('command palette can open the agent panel', async ({ page }) => {
	await mockHome(page);
	await gotoShell(page, '/');
	await page.keyboard.press('Control+k');
	const palette = page.getByRole('dialog', { name: 'Command palette' });
	await palette.getByRole('combobox').fill('agent');
	await palette.getByRole('option', { name: 'Open agent' }).click();
	await expect(palette).toBeHidden();
	await expect(page.getByRole('complementary', { name: 'Agent' })).toBeVisible();
});

test('theme toggle switches palettes and persists across reloads', async ({ page }) => {
	await page.emulateMedia({ colorScheme: 'dark' });
	await mockHome(page);
	await gotoShell(page, '/');
	const html = page.locator('html');
	await expect(html).toHaveAttribute('data-theme', 'dark');
	const toggle = page.getByTestId('theme-toggle');
	await expect(toggle).toHaveAccessibleName('Switch to light theme');
	const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);

	await toggle.click();
	await expect(html).toHaveAttribute('data-theme', 'light');
	await expect(toggle).toHaveAccessibleName('Switch to dark theme');
	const lightBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
	expect(lightBg).not.toBe(darkBg);

	await page.reload();
	await expect(html).toHaveAttribute('data-theme', 'light');
	expect(await page.evaluate(() => localStorage.getItem('thytrader.theme'))).toBe('light');
});

test('theme follows prefers-color-scheme until the viewer chooses', async ({ page }) => {
	await page.emulateMedia({ colorScheme: 'light' });
	await mockHome(page);
	await gotoShell(page, '/');
	await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});
