import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';

/**
 * Typed client for in-app operator chat. LLM keys are write-only.
 */

export type LlmProvider = 'openai' | 'openai_compatible';

export interface OperatorChatStatus {
	schema_version: 'thytrader-operator-chat-v1';
	llm_configured: boolean;
	provider: string | null;
	model: string | null;
	base_url: string | null;
	key_storage: 'api_process';
	coinbase_credentials_in_chat: false;
}

export interface ChatMessage {
	id: string;
	role: 'system' | 'user' | 'assistant' | 'tool';
	content: string;
	tool_call_id: string | null;
	tool_name: string | null;
	created_at: string;
}

export interface PendingConfirmation {
	id: string;
	lane: 'operator' | 'data' | 'research' | 'runtime' | 'playbook' | 'memory';
	tool_name: string;
	summary: string;
	arguments: Record<string, unknown>;
	requires_understand_live: boolean;
	tool_call_id: string;
}

export interface ChatTranscript {
	schema_version: 'thytrader-operator-chat-v1';
	status: OperatorChatStatus;
	messages: ChatMessage[];
	pending_confirmations: PendingConfirmation[];
}

export interface LlmCredentialWrite {
	provider: LlmProvider;
	model: string;
	base_url?: string | null;
	api_key: string;
}

async function readJson<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
	const method = init?.method?.toUpperCase() ?? 'GET';
	if (method !== 'GET' && method !== 'HEAD') {
		await ensureBrowserCsrfSession();
	}
	const response = await fetch(path, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(init?.headers ?? {}),
			...mutationHeaders()
		}
	});
	if (!response.ok) {
		throw new Error(await errorMessage(response, fallback));
	}
	return (await response.json()) as T;
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
	try {
		const payload: unknown = await response.json();
		if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
			const detail = (payload as { detail: unknown }).detail;
			if (typeof detail === 'string' && detail.length > 0) {
				return detail;
			}
		}
	} catch {
		return fallback;
	}
	return fallback;
}

export async function fetchOperatorChatStatus(): Promise<OperatorChatStatus> {
	return readJson<OperatorChatStatus>(
		'/api/v1/operator-chat/status',
		'Operator chat status is unavailable.'
	);
}

export async function fetchOperatorChatTranscript(): Promise<ChatTranscript> {
	return readJson<ChatTranscript>(
		'/api/v1/operator-chat/transcript',
		'Operator chat transcript is unavailable.'
	);
}

export async function saveLlmCredentials(body: LlmCredentialWrite): Promise<OperatorChatStatus> {
	return readJson<OperatorChatStatus>(
		'/api/v1/operator-chat/credentials',
		'LLM key could not be stored.',
		{ method: 'PUT', body: JSON.stringify(body) }
	);
}

export async function clearLlmCredentials(): Promise<OperatorChatStatus> {
	return readJson<OperatorChatStatus>(
		'/api/v1/operator-chat/credentials',
		'LLM key could not be cleared.',
		{ method: 'DELETE' }
	);
}

export async function sendOperatorChatMessage(content: string): Promise<ChatTranscript> {
	return readJson<ChatTranscript>(
		'/api/v1/operator-chat/messages',
		'Operator chat could not send that message.',
		{ method: 'POST', body: JSON.stringify({ content }) }
	);
}

export async function decideOperatorChatConfirmation(
	id: string,
	body: { confirmed: boolean; i_understand_live: boolean }
): Promise<ChatTranscript> {
	return readJson<ChatTranscript>(
		`/api/v1/operator-chat/confirmations/${id}`,
		'Operator chat could not record that confirmation.',
		{ method: 'POST', body: JSON.stringify(body) }
	);
}

export async function resetOperatorChat(): Promise<ChatTranscript> {
	return readJson<ChatTranscript>('/api/v1/operator-chat/reset', 'Operator chat could not reset.', {
		method: 'POST'
	});
}

export function visibleChatMessages(messages: ChatMessage[]): ChatMessage[] {
	return messages.filter(
		(row) => row.role === 'user' || (row.role === 'assistant' && row.content.trim() !== '')
	);
}
