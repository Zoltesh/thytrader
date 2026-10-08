/**
 * Durable per-bar decision journal (`thytrader-bar-decision-v1`).
 *
 * The runtime journals one row per (deployment, product, bar start) for every
 * completed bar of every paper and live strategy bot: what it decided, the
 * server-written one-line reason, the evaluated rule tree with actual values,
 * the risk verdict, and any order action. Two read-only endpoints serve it,
 * newest first with opaque cursor paging:
 *
 * - `GET /api/v1/deployments/{id}/decisions` (one bot)
 * - `GET /api/v1/strategies/{id}/decisions` (every bot of one strategy,
 *   optionally narrowed to one `deployment_id`)
 *
 * Everything else here is a pure presentation helper over those payloads.
 * Decimals stay exact strings: the compact formatter only shortens what is
 * displayed, and every rounded value keeps its raw string for a tooltip.
 *
 * This module is the public barrel. The code lives in focused modules that never
 * import this barrel:
 * - `decisions-types.ts`: journal row and response payload types.
 * - `decisions-api.ts`: filters, request paths, the HTTP client, and page validation.
 * - `decisions-rows.ts`: row identity, paging merges, and trade-reason assignment.
 * - `decisions-format.ts`: labels, tones, and chip/row text.
 * - `decisions-numbers.ts`: compact decimals and the next-evaluation clock.
 */
export { BAR_DECISION_SCHEMA_VERSION } from './decisions-types';
export type {
	BarDecision,
	ComparisonNode,
	ComparisonOperator,
	ConditionGroupNode,
	ConditionNode,
	ConditionOperand,
	ConditionResult,
	DecisionAction,
	DecisionExitReason,
	DecisionExitRule,
	DecisionFill,
	DecisionHtfFilter,
	DecisionIndicatorValue,
	DecisionOrder,
	DecisionOutcome,
	DecisionPage,
	DecisionPosition,
	DecisionRisk,
	DecisionRule,
	DecisionSignal,
	DecisionSkipReason,
	DecisionStorage,
	DeploymentDecisionsResponse,
	IntentPurpose,
	RuleOutcome,
	StrategyDecisionsResponse
} from './decisions-types';
export {
	DECISION_FILTERS,
	DECISION_PAGE_LIMIT_MAX,
	DECISION_PAGE_SIZE,
	DecisionApiError,
	decisionFilterOutcomes,
	deploymentDecisionsPath,
	fetchDeploymentDecisions,
	fetchStrategyDecisions,
	strategyDecisionsPath,
	validateDecisionPage
} from './decisions-api';
export type { DecisionFilter, DecisionQuery, StrategyDecisionQuery } from './decisions-api';
export {
	appendDecisionPage,
	assignTradeReasons,
	decisionIntentIds,
	decisionKey,
	decisionProducts
} from './decisions-rows';
export type { TradeReasonAssignment } from './decisions-rows';
export {
	barSpanText,
	conditionChipText,
	conditionChipTitle,
	conditionGroupDescription,
	conditionGroupText,
	conditionOperandText,
	conditionResultGlyph,
	conditionResultLabel,
	DECISION_RETENTION_NOTE,
	decisionActionLabel,
	decisionBotLabel,
	decisionEmptyText,
	decisionOutcomeLabel,
	decisionOutcomeTone,
	exitReasonLabel,
	htfFilterChipText,
	intentPurposeLabel,
	isCrossover,
	positionSnapshotText,
	riskVerdictText,
	ruleOutcomeLabel,
	ruleOutcomeResult,
	skipReasonLabel
} from './decisions-format';
export type { DecisionTone } from './decisions-format';
export { formatDecimalCompact, nextEvaluationAt, nextEvaluationText } from './decisions-numbers';
