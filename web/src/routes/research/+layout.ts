import { RESEARCH_CONTEXT_LABEL } from '$lib/workstation-chrome';
import type { LayoutLoad } from './$types';

/**
 * Research stays a research-only environment label, not a health signal.
 */
export const load: LayoutLoad = (): { contextLabel: string } => {
	return { contextLabel: RESEARCH_CONTEXT_LABEL };
};
