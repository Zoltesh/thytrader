import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: './src',
	testMatch: '**/*.e2e.{ts,js}',
	use: { baseURL: 'http://127.0.0.1:4173' },
	webServer: [
		{
			command: 'uv run thytrader-api',
			cwd: '..',
			url: 'http://127.0.0.1:8200/health/ready',
			env: {
				THYTRADER_API_PORT: '8200',
				THYTRADER_COINBASE_API_KEY_NAME: '',
				THYTRADER_COINBASE_API_PRIVATE_KEY: '',
				THYTRADER_DATABASE_URL: '',
				THYTRADER_ENVIRONMENT: 'test'
			},
			reuseExistingServer: true
		},
		{
			command: 'npm run dev -- --host 127.0.0.1 --port 4173',
			url: 'http://127.0.0.1:4173',
			reuseExistingServer: true
		}
	]
});
