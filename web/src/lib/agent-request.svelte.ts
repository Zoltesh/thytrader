/**
 * Open the shell's Agent panel from a page with something to ask (ADR 0091).
 *
 * "Ask why" on a manager proposal calls `askAgent()` with the proposal as the
 * panel's context and a drafted question; the layout opens the panel, shows the
 * context on its "Looking at" line, and pre-fills the chat draft. Nothing is
 * sent until the operator presses Send, and the chat keeps its own gates: it can
 * never approve, decline, or place an order on the operator's behalf.
 */

export type AgentRequest = {
	/** Increments on every request so the layout can react to repeats. */
	sequence: number;
	/** Shown on the panel's "Looking at" line instead of the route label. */
	context: string | null;
	/** Pre-filled chat draft (editable; never auto-sent). */
	draft: string | null;
};

class AgentRequests {
	current = $state<AgentRequest>({ sequence: 0, context: null, draft: null });

	ask(context: string, draft: string): void {
		this.current = { sequence: this.current.sequence + 1, context, draft };
	}

	clear(): void {
		this.current = { sequence: this.current.sequence, context: null, draft: null };
	}
}

export const agentRequests = new AgentRequests();

/** Ask the Agent panel about something on this page. */
export function askAgent(context: string, draft: string): void {
	agentRequests.ask(context, draft);
}
