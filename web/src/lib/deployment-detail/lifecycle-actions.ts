/**
 * Lifecycle-action plumbing of the bot detail page: which endpoint a confirmed
 * dialog action calls, which acceptance message it earns, and how an ambiguous
 * transport failure is recognised (an unknown outcome is never retried).
 */
import type { LifecycleAction } from '$lib/deployment-detail';
import {
	pauseDeployment,
	resetBreakerLatches,
	resumeDeployment,
	stopDeployment,
	type Deployment
} from '$lib/deployments';

/**
 * Send one confirmed lifecycle action. `liveAcknowledged` is true only for a
 * live deployment whose dialog checkbox was ticked; `stopWithFlatten` applies
 * to `stop` only (`flatten` always flattens).
 */
export function sendLifecycleAction(
	action: LifecycleAction,
	deploymentId: string,
	options: { liveAcknowledged: boolean; stopWithFlatten: boolean }
): Promise<Deployment> {
	if (action === 'pause') return pauseDeployment(deploymentId);
	if (action === 'resume')
		// Live resume sends i_understand_live only after the dialog's ticked checkbox.
		return resumeDeployment(deploymentId, { liveAcknowledged: options.liveAcknowledged });
	if (action === 'flatten') return stopDeployment(deploymentId, true);
	if (action === 'reset-breakers') return resetBreakerLatches(deploymentId);
	return stopDeployment(deploymentId, options.stopWithFlatten);
}

/** The action an acceptance message names: a stop that flattens reads as a flatten. */
export function acceptedAction(action: LifecycleAction, stopWithFlatten: boolean): LifecycleAction {
	return stopWithFlatten && action === 'stop' ? 'flatten' : action;
}

/** A browser network failure: the request may or may not have reached the server. */
export function isAmbiguousTransportFailure(message: string): boolean {
	return message === 'Failed to fetch' || message.includes('NetworkError');
}

/** Latched circuit breakers, joined for the breaker row (empty when none is latched). */
export function breakerLatchText(deployment: {
	daily_loss_latched: boolean;
	drawdown_latched: boolean;
}): string {
	return [
		deployment.daily_loss_latched ? 'Daily-loss breaker latched' : null,
		deployment.drawdown_latched ? 'Drawdown breaker latched' : null
	]
		.filter(Boolean)
		.join(' · ');
}
