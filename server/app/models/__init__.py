"""Модель данных движка (engine-ТЗ §22).

Импорт всех таблиц в одном месте нужен, чтобы `Base.metadata` знала о них к
моменту автогенерации миграции: таблица, которую забыли импортировать,
молча исчезает из схемы.
"""

from .base import Base
from .device import DeviceCredential, DevicePairingSession
from .datasets import DatasetSnapshot
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
from .notifications import NotificationOutbox
from .paper import PaperTrade
from .paper_ab import PaperAbDecision, PaperAbOutcome
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
from .market import (
    Bar,
    DataQualityEvent,
    FxRate,
    Instrument,
    MarketFeature,
    RegimeSnapshot,
)
from .portfolio import (
    Account,
    Holding,
    PortfolioModel,
    PortfolioRun,
    PortfolioWeight,
    RebalanceDraft,
)
from .risk import AuditEvent, BacktestRun, ModelRegistry, RiskSnapshot, RiskState
from .retention import RetentionAttemptIntent, RetentionAttemptOutcome
from .shadow import ShadowObservation
from .strategies import StrategyPromotionEvent, StrategyVersion

__all__ = [
    "Base",
    "DeviceCredential",
    "DevicePairingSession",
    "DatasetSnapshot",
    "Instrument",
    "Bar",
    "MarketFeature",
    "RegimeSnapshot",
    "DataQualityEvent",
    "FxRate",
    "PaperTrade",
    "TradeIdea",
    "IdeaEvent",
    "IdeaOutcome",
    "IdeaSkip",
    "ShadowObservation",
    "PaperAbDecision",
    "PaperAbOutcome",
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
    "NotificationOutbox",
    "Account",
    "Holding",
    "EquityRankingSnapshot",
    "PortfolioModel",
    "PortfolioRun",
    "PortfolioWeight",
    "RebalanceDraft",
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
    "CollectionPermit",
    "HypothesisEvidence",
    "ResearchHypothesis",
    "ResearchObservation",
    "ResearchSignal",
    "ResearchSource",
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
