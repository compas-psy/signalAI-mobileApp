"""Модель данных движка (engine-ТЗ §22).

Импорт всех таблиц в одном месте нужен, чтобы `Base.metadata` знала о них к
моменту автогенерации миграции: таблица, которую забыли импортировать,
молча исчезает из схемы.
"""

from .base import Base
from .canary_evidence import CanaryEvidenceReference
from .canary_policy import CanaryPolicySnapshot, LighterCredentialGeneration
from .datasets import DatasetSnapshot
from .device import (
    DeviceCredential,
    DeviceOwnerKey,
    DevicePairingSession,
    OwnerStepUpChallenge,
)
from .enums import (
    AssetClass,
    BarrierOutcome,
    DerivativesFlow,
    Direction,
    EvidenceRole,
    ExecutionMode,
    FactLabel,
    HypothesisState,
    IdeaStatus,
    LicenseStatus,
    LiquidityRegime,
    OrderIntent,
    PackageSize,
    QualityFlag,
    QualityStatus,
    ResearchDirection,
    RiskProfile,
    SignalStatus,
    SkipReason,
    Strategy,
    SwingState,
    Timeframe,
    TrendRegime,
    Venue,
    VolatilityRegime,
)
from .equity_ranking import EquityRankingSnapshot
from .execution import (
    ExecutionFill,
    ExecutionIntent,
    ExecutionModeActivationRequest,
    ExecutionModeEvent,
    ExecutionModeState,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
    ExecutionRiskOverride,
    ExecutionVenueHealth,
)
from .experiments import (
    Experiment,
    ExperimentArm,
    ExperimentMetric,
    ExperimentRun,
    PromotionDecision,
)
from .ideas import IdeaEvent, IdeaOutcome, IdeaSkip, TradeIdea
from .lighter_execution import (
    LighterNonceReservation,
    LighterOrderActionBinding,
    LighterOrderIdentity,
    LighterReconciliationEvidence,
    LighterTestnetSmokeEvidence,
)
from .management import ExecutionManagementPolicySnapshot
from .manual_control import ExecutionManualTradeControl
from .market import (
    Bar,
    DataQualityEvent,
    FxRate,
    Instrument,
    MarketFeature,
    RegimeSnapshot,
)
from .notifications import NotificationOutbox
from .paper import PaperTrade
from .paper_ab import PaperAbDecision, PaperAbOutcome
from .portfolio import (
    Account,
    Holding,
    PortfolioModel,
    PortfolioRun,
    PortfolioWeight,
    RebalanceDraft,
)
from .promotion_evidence import PromotionEvidenceDecision, PromotionEvidenceSnapshot
from .research import (
    CollectionPermit,
    HypothesisEvidence,
    ResearchHypothesis,
    ResearchObservation,
    ResearchSignal,
    ResearchSource,
)
from .research_trials import (
    ResearchSearchCampaign,
    ResearchTrial,
    ResearchTrialOutcome,
)
from .retention import RetentionAttemptIntent, RetentionAttemptOutcome
from .risk import AuditEvent, BacktestRun, ModelRegistry, RiskSnapshot, RiskState
from .shadow import ShadowObservation
from .strategies import StrategyPromotionEvent, StrategyVersion
from .tinvest_sandbox import TInvestSandboxRoundTripProof

__all__ = [
    "Base",
    # dataset lineage
    "DatasetSnapshot",
    # market
    "Instrument",
    "Bar",
    "MarketFeature",
    "RegimeSnapshot",
    "DataQualityEvent",
    "FxRate",
    # ideas / owner paper
    "PaperTrade",
    "TradeIdea",
    "IdeaEvent",
    "IdeaOutcome",
    "IdeaSkip",
    # isolated candidate measurement
    "ShadowObservation",
    "PaperAbDecision",
    "PaperAbOutcome",
    # durable execution domain
    "ExecutionModeState",
    "ExecutionModeEvent",
    "ExecutionModeActivationRequest",
    "ExecutionRiskOverride",
    "ExecutionIntent",
    "ExecutionOrder",
    "ExecutionFill",
    "ExecutionProtection",
    "ExecutionReconciliationEvent",
    "ExecutionVenueHealth",
    "ExecutionManagementPolicySnapshot",
    "ExecutionManualTradeControl",
    "LighterOrderIdentity",
    "LighterOrderActionBinding",
    "LighterNonceReservation",
    "LighterReconciliationEvidence",
    "LighterTestnetSmokeEvidence",
    "LighterCredentialGeneration",
    "CanaryPolicySnapshot",
    "CanaryEvidenceReference",
    "TInvestSandboxRoundTripProof",
    # device lifecycle
    "DeviceCredential",
    "DevicePairingSession",
    "DeviceOwnerKey",
    "OwnerStepUpChallenge",
    # delivery
    "NotificationOutbox",
    # portfolio
    "Account",
    "Holding",
    "EquityRankingSnapshot",
    "PortfolioModel",
    "PortfolioRun",
    "PortfolioWeight",
    "RebalanceDraft",
    # risk / research / strategy governance / experiments
    "RiskSnapshot",
    "RiskState",
    "AuditEvent",
    "RetentionAttemptIntent",
    "RetentionAttemptOutcome",
    "BacktestRun",
    "ModelRegistry",
    "StrategyVersion",
    "StrategyPromotionEvent",
    "ResearchSearchCampaign",
    "ResearchTrial",
    "ResearchTrialOutcome",
    "Experiment",
    "ExperimentArm",
    "ExperimentRun",
    "ExperimentMetric",
    "PromotionDecision",
    "PromotionEvidenceDecision",
    "PromotionEvidenceSnapshot",
    # early signals
    "CollectionPermit",
    "HypothesisEvidence",
    "ResearchHypothesis",
    "ResearchObservation",
    "ResearchSignal",
    "ResearchSource",
    # enums
    "AssetClass",
    "BarrierOutcome",
    "DerivativesFlow",
    "Direction",
    "EvidenceRole",
    "ExecutionMode",
    "FactLabel",
    "HypothesisState",
    "IdeaStatus",
    "LicenseStatus",
    "LiquidityRegime",
    "OrderIntent",
    "PackageSize",
    "QualityFlag",
    "QualityStatus",
    "ResearchDirection",
    "RiskProfile",
    "SignalStatus",
    "SkipReason",
    "Strategy",
    "SwingState",
    "Timeframe",
    "TrendRegime",
    "Venue",
    "VolatilityRegime",
]
