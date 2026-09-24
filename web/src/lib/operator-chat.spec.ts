import { describe, expect, it, vi } from 'vitest';

import { sendOperatorChatMessage, visibleChatMessages, type ChatMessage } from './operator-chat';

describe('operator chat client helpers', () => {
	it('shows only user and assistant rows in the transcript pane', () => {
		const rows: ChatMessage[] = [
			{
				id: '1',
				role: 'user',
				content: 'health?',
				tool_call_id: null,
				tool_name: null,
				created_at: '2026-09-16T00:00:00Z'
			},
			{
				id: '2',
				role: 'tool',
				content: '{"overall_status":"healthy"}',
				tool_call_id: 'call_1',
				tool_name: 'operator_health',
				created_at: '2026-09-16T00:00:01Z'
			},
			{
				id: '3',
				role: 'assistant',
				content: 'API is healthy.',
				tool_call_id: null,
				tool_name: null,
				created_at: '2026-09-16T00:00:02Z'
			}
		];
		expect(visibleChatMessages(rows).map((row) => row.role)).toEqual(['user', 'assistant']);
	});

	it('hides empty assistant tool-call placeholders', () => {
		const rows: ChatMessage[] = [
			{
				id: '1',
				role: 'assistant',
				content: '   ',
				tool_call_id: null,
				tool_name: null,
				created_at: '2026-09-16T00:00:00Z'
			},
			{
				id: '2',
				role: 'user',
				content: 'health?',
				tool_call_id: null,
				tool_name: null,
				created_at: '2026-09-16T00:00:01Z'
			}
		];
		expect(visibleChatMessages(rows).map((row) => row.id)).toEqual(['2']);
	});

	it('establishes CSRF before sending a chat mutation', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ csrf_token: 'chat-test-csrf' }) })
			.mockResolvedValueOnce({ ok: true, json: async () => ({ messages: [] }) });
		vi.stubGlobal('fetch', fetchMock);
		try {
			await sendOperatorChatMessage('health?');
			expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/security/session', {
				headers: { Accept: 'application/json' }
			});
			expect(fetchMock).toHaveBeenNthCalledWith(
				2,
				'/api/v1/operator-chat/messages',
				expect.objectContaining({
					method: 'POST',
					headers: expect.objectContaining({ 'X-CSRF-Token': 'chat-test-csrf' })
				})
			);
		} finally {
			vi.unstubAllGlobals();
		}
	});
});
