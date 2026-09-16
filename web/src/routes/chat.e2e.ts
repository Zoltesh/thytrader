import { expect, test } from '../e2e/harness';

const status = {
	schema_version: 'thytrader-operator-chat-v1',
	llm_configured: false,
	provider: null,
	model: null,
	base_url: null,
	key_storage: 'api_process',
	coinbase_credentials_in_chat: false
};

const emptyTranscript = {
	schema_version: 'thytrader-operator-chat-v1',
	status,
	messages: [],
	pending_confirmations: []
};

test('operator chat stores an LLM key without echoing it and confirms mutations', async ({
	page
}) => {
	let configured = false;
	const pendingId = '01985cf0-7b60-7000-8000-0000000000aa';
	await page.route('**/api/v1/operator-chat/**', async (route) => {
		const url = new URL(route.request().url());
		const method = route.request().method();
		if (url.pathname === '/api/v1/operator-chat/credentials' && method === 'PUT') {
			const body = route.request().postDataJSON() as { api_key?: string };
			expect(body.api_key).toBe('sk-test-operator-chat-key');
			configured = true;
			await route.fulfill({
				json: {
					...status,
					llm_configured: true,
					provider: 'openai',
					model: 'gpt-4o-mini',
					base_url: 'https://api.openai.com/v1'
				}
			});
			return;
		}
		if (url.pathname === '/api/v1/operator-chat/transcript' && method === 'GET') {
			await route.fulfill({
				json: {
					...emptyTranscript,
					status: {
						...status,
						llm_configured: configured,
						provider: configured ? 'openai' : null,
						model: configured ? 'gpt-4o-mini' : null,
						base_url: configured ? 'https://api.openai.com/v1' : null
					}
				}
			});
			return;
		}
		if (url.pathname === '/api/v1/operator-chat/messages' && method === 'POST') {
			await route.fulfill({
				json: {
					schema_version: 'thytrader-operator-chat-v1',
					status: {
						...status,
						llm_configured: true,
						provider: 'openai',
						model: 'gpt-4o-mini',
						base_url: 'https://api.openai.com/v1'
					},
					messages: [
						{
							id: '01985cf0-7b60-7000-8000-0000000000ab',
							role: 'user',
							content: 'Start live for the published fingerprint',
							tool_call_id: null,
							tool_name: null,
							created_at: '2026-09-16T07:00:00Z'
						},
						{
							id: '01985cf0-7b60-7000-8000-0000000000ac',
							role: 'assistant',
							content: 'Live start is queued for confirmation.',
							tool_call_id: null,
							tool_name: null,
							created_at: '2026-09-16T07:00:01Z'
						}
					],
					pending_confirmations: [
						{
							id: pendingId,
							lane: 'runtime',
							tool_name: 'runtime_start',
							summary: 'runtime:runtime_start · mode=live · requires understand-live',
							arguments: { mode: 'live', strategy_fingerprint: 'sha256:' + 'a'.repeat(64) },
							requires_understand_live: true,
							tool_call_id: 'call_live'
						}
					]
				}
			});
			return;
		}
		if (url.pathname === `/api/v1/operator-chat/confirmations/${pendingId}` && method === 'POST') {
			const body = route.request().postDataJSON() as {
				confirmed?: boolean;
				i_understand_live?: boolean;
			};
			expect(body.confirmed).toBe(true);
			expect(body.i_understand_live).toBe(true);
			await route.fulfill({
				json: {
					schema_version: 'thytrader-operator-chat-v1',
					status: {
						...status,
						llm_configured: true,
						provider: 'openai',
						model: 'gpt-4o-mini',
						base_url: 'https://api.openai.com/v1'
					},
					messages: [
						{
							id: '01985cf0-7b60-7000-8000-0000000000ad',
							role: 'assistant',
							content: 'Live start was submitted through the runtime HTTP contract.',
							tool_call_id: null,
							tool_name: null,
							created_at: '2026-09-16T07:00:02Z'
						}
					],
					pending_confirmations: []
				}
			});
			return;
		}
		await route.fulfill({ json: emptyTranscript });
	});

	await page.goto('/chat');
	await expect(page.getByRole('heading', { name: 'Operator chat' })).toBeVisible();
	await expect(page.getByText('This is not a Coinbase form')).toBeVisible();
	await expect(page.getByTestId('llm-configured')).toContainText('Not configured');

	await page.getByTestId('llm-api-key').fill('sk-test-operator-chat-key');
	await page.getByTestId('save-llm-key').click();
	await expect(page.getByTestId('llm-configured')).toContainText('Configured');
	await expect(page.getByTestId('llm-configured')).not.toContainText('sk-test');

	await page.getByTestId('chat-draft').fill('Start live for the published fingerprint');
	await page.getByTestId('send-chat').click();
	await expect(page.getByTestId('pending-confirmations')).toBeVisible();
	await page.getByTestId('understand-live').check();
	await page.getByTestId('confirm-mutation').click();
	await expect(
		page.getByText('Live start was submitted through the runtime HTTP contract.')
	).toBeVisible();
	await expect(page.getByTestId('pending-confirmations')).toHaveCount(0);
});
