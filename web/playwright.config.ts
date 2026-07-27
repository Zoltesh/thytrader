import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: './src',
	testMatch: '**/*.e2e.{ts,js}',
	use: { baseURL: 'http://127.0.0.1:4173' },
	webServer: [
		{
			command: 'uv run thytrader-api',
			cwd: '..',
			url: 'http://127.0.0.1:8000/health/ready',
			reuseExistingServer: true
		},
		{
			command: 'npm run dev -- --host 127.0.0.1 --port 4173',
			url: 'http://127.0.0.1:4173',
			reuseExistingServer: true
		}
	]
});
