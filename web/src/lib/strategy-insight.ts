/**
 * Strategy insight: plain-language text and client-side validation of a builder
 * model.
 *
 * This module is the public barrel. The code lives in focused modules that never
 * import this barrel:
 * - `strategy-insight-text.ts`: rule text, the summary, reference-gate and signal-exit
 *   sentences, and the required-data text.
 * - `strategy-insight-conditions.ts`: rule-tree structure, operand lags and rule-tree checks.
 * - `strategy-insight-references.ts`: reference-instrument checks (ADR 0096).
 * - `strategy-insight-validation.ts`: whole-definition validation.
 */
export {
	OPERATOR_LABELS,
	conditionToText,
	plainEnglishSummary,
	referenceGateSentence,
	requiredDataText,
	signalExitSentence
} from './strategy-insight-text';
export { validateReferenceInstruments } from './strategy-insight-references';
export { validateDefinition } from './strategy-insight-validation';
