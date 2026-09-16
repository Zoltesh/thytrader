import { OPERATOR_CHAT_CONTEXT_LABEL } from '$lib/workstation-chrome';
import type { LayoutLoad } from './$types';

/**
 * Operator chat keeps a distinct context pill. It is not a Coinbase secrets page.
 */
export const load: LayoutLoad = (): { contextLabel: string } => {
	return { contextLabel: OPERATOR_CHAT_CONTEXT_LABEL };
};
