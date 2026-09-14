import { defineConfig } from '@playwright/test';

const e2eApiPort = 18200;
const e2eUiPort = 14173;
const e2eApiOrigin = `http://127.0.0.1:${e2eApiPort}`;

export default defineConfig({
	testDir: './src',
	testMatch: '**/*.e2e.{ts,js}',
	fullyParallel: false,
	workers: 1,
	retries: 2,
	timeout: 60_000,
	expect: { timeout: 15_000 },
	use: { baseURL: `http://127.0.0.1:${e2eUiPort}` },
	webServer: [
		{
			command: 'uv run thytrader-api',
			cwd: '..',
			url: `${e2eApiOrigin}/health/ready`,
			timeout: 120_000,
			reuseExistingServer: false,
			env: {
				THYTRADER_API_PORT: String(e2eApiPort),
				THYTRADER_COINBASE_API_KEY_NAME: '',
				THYTRADER_COINBASE_API_PRIVATE_KEY: '',
				THYTRADER_DATABASE_URL: '',
				THYTRADER_ENVIRONMENT: 'test'
			}
		},
		{
			command: `npm run dev -- --host 127.0.0.1 --port ${e2eUiPort}`,
			url: `http://127.0.0.1:${e2eUiPort}`,
			timeout: 120_000,
			reuseExistingServer: false,
			env: {
				THYTRADER_API_PROXY_TARGET: e2eApiOrigin
			}
		}
	]
});
