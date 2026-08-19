"""Модель данных движка (engine-ТЗ §22).

Импорт всех таблиц в одном месте нужен, чтобы `Base.metadata` знала о них к
моменту автогенерации миграции: таблица, которую забыли импортировать,
молча исчезает из схемы.
"""

from .base import Base
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
    ExecutionModeEvent,
    ExecutionModeState,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
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
from .notifications import NotificationOutbox
from .paper import PaperTrade
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
from .strategies import StrategyPromotionEvent, StrategyVersion

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
    # ideas
    "PaperTrade",
    "TradeIdea",
    "IdeaEvent",
    "IdeaOutcome",
    "IdeaSkip",
    # durable execution domain
    "ExecutionModeState",
    "ExecutionModeEvent",
    "ExecutionIntent",
    "ExecutionOrder",
    "ExecutionFill",
    "ExecutionProtection",
    "ExecutionReconciliationEvent",
    "ExecutionVenueHealth",
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
