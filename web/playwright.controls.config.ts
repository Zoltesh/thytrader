// Isolated test-only follow-up config. Run with env -i (see slice completion note).
import { defineConfig } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, readdirSync } from 'node:fs';

for (const directory of ['.', '..']) {
	if (
		readdirSync(directory).some(
			(name) => name === '.env' || (name.startsWith('.env.') && name !== '.env.example')
		)
	) {
		throw new Error('Refusing browser tests in a checkout containing dotenv configuration.');
	}
}
const token = randomBytes(32).toString('hex');
const credentials = mkdtempSync('/tmp/tt-controls-e2e-');
export default defineConfig({
	testDir: './src',
	testMatch: ['routes/deployments.e2e.ts', 'routes/fleet-controls.e2e.ts'],
	workers: 1,
	retries: 0,
	timeout: 30000,
	expect: { timeout: 10000 },
	use: { baseURL: 'http://127.0.0.1:24167' },
	webServer: [
		{
			command: 'uv run python -m tests.fleet_control.browser_api',
			cwd: '..',
			url: 'http://127.0.0.1:28167/health/ready',
			reuseExistingServer: false,
			timeout: 90000,
			env: { CONTROLS_TEST_INSTALLATION_TOKEN: token, CONTROLS_TEST_CREDENTIALS_DIR: credentials }
		},
		{
			command: 'npm run dev -- --host 127.0.0.1 --port 24167',
			url: 'http://127.0.0.1:24167',
			reuseExistingServer: false,
			timeout: 90000,
			env: {
				THYTRADER_API_PROXY_TARGET: 'http://127.0.0.1:28167',
				THYTRADER_INSTALLATION_TOKEN: token
			}
		}
	]
});
