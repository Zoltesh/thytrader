import { RESEARCH_CONTEXT_LABEL } from '$lib/workstation-chrome';
import type { LayoutLoad } from './$types';

/**
 * Strategy library and builder keep the research-only context pill.
 */
export const load: LayoutLoad = (): { contextLabel: string } => {
	return { contextLabel: RESEARCH_CONTEXT_LABEL };
};
