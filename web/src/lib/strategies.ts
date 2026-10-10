/**
 * Strategies (ADR 0082): API client, builder model, and pure helpers.
 *
 * This module is the public barrel. The code lives in focused modules that never
 * import this barrel:
 * - `strategies-types.ts`: strategy record, library, deletion, snapshot, and dataset types.
 * - `strategies-indicators.ts`: indicator drafts, kind options, kind defaults, and serialization.
 * - `strategies-operands.ts`: condition and operand drafts, operand keys, choices, and labels.
 * - `strategies-timeframes.ts`: execution clocks, dataset windows, and UTC input values.
 * - `strategies-builder.ts`: builder model, its defaults, and conversion to and from definitions.
 * - `strategies-library.ts`: New-strategy templates, library origin views, and deletion text.
 * - `strategies-api.ts`: HTTP client and `StrategyApiError`.
 */
export type {
	BacktestLaunchInput,
	BulkDeleteItem,
	BulkDeleteOutcome,
	BulkDeleteResponse,
	Dataset,
	StrategyDefinition,
	StrategyDeletionCounts,
	StrategyDeletionResult,
	StrategyDocument,
	StrategyLibraryBacktest,
	StrategyLibraryEntry,
	StrategyLibraryPaperLive,
	StrategyRecord,
	StrategySnapshot,
	StrategyValidation,
	StrategyValidationIssue,
	StrategyValidationWarning
} from './strategies-types';
export {
	applyIndicatorKindDefaults,
	IDENTITY_INPUT_OPTIONS,
	INDICATOR_KIND_OPTIONS,
	INDICATOR_OUTPUT_SERIES,
	isConfigurableRollingKind,
	MAX_REFERENCE_INSTRUMENTS,
	serializeIndicator
} from './strategies-indicators';
export type {
	IdentityInput,
	IndicatorDraft,
	IndicatorInput,
	ReferenceInstrumentDraft
} from './strategies-indicators';
export {
	defaultIndicatorOperand,
	indicatorOperandKey,
	indicatorReference,
	operandChoices,
	parseIndicatorOperandKey,
	referenceBaseLabel,
	referenceOperandLabel
} from './strategies-operands';
export type {
	ComparisonOperatorValue,
	ConditionDraft,
	OperandChoice,
	OperandDraft
} from './strategies-operands';
export {
	datasetEvaluationWindow,
	EXECUTION_TIMEFRAMES,
	extraIndicatorTimeframes,
	formatUtcInputValue,
	latestDatasets,
	parseUtcInputValue,
	researchWindowHint,
	resolvedIndicatorTimeframe,
	unboundIndicatorTimeframes,
	validHtfTimeframes,
	validReferenceTimeframes
} from './strategies-timeframes';
export type { ExecutionTimeframe } from './strategies-timeframes';
export {
	builderModelFromRecord,
	builderQuoteLabel,
	defaultHtfFilter,
	defaultReferenceInstrument,
	defaultSignalExit,
	fromBuilderModel,
	quoteCurrencyFor,
	quoteLabelFor,
	takeProfitMultiple,
	takeProfitPhrase,
	toBuilderModel
} from './strategies-builder';
export {
	defaultDerivatives,
	MAX_STRATEGY_LEVERAGE,
	MIN_STRATEGY_LEVERAGE
} from './strategies-derivatives';
export type { DerivativesDraft, InstrumentKind } from './strategies-derivatives';
export type {
	BuilderModel,
	CoveredInstrumentDraft,
	HtfFilterDraft,
	PyramidingDraft,
	SignalExitDraft,
	TakeProfitDraft
} from './strategies-builder';
export {
	bulkOutcomeText,
	deletionCountsText,
	isResearchTag,
	readStoredOrigin,
	rememberOrigin,
	STRATEGY_ORIGIN_OPTIONS,
	STRATEGY_TEMPLATE_OPTIONS
} from './strategies-library';
export type { StrategyOrigin, StrategyTemplateOption } from './strategies-library';
export {
	bulkDeleteStrategies,
	cloneStrategy,
	createStrategy,
	deleteStrategy,
	fetchStrategy,
	fetchStrategyPage,
	fetchStrategySnapshot,
	importStrategy,
	listDatasets,
	listStrategies,
	saveStrategy,
	StrategyApiError,
	strategyErrorCode,
	submitBacktest
} from './strategies-api';
export type { IndicatorKindValue, IndicatorParameters } from './indicator-catalog';
