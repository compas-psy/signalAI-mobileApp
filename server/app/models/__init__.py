"""Модель данных движка (engine-ТЗ §22).

Импорт всех таблиц в одном месте нужен, чтобы `Base.metadata` знала о них к
моменту автогенерации миграции: таблица, которую забыли импортировать,
молча исчезает из схемы.
"""

from .base import Base
from .enums import (
    AssetClass,
    BarrierOutcome,
    DerivativesFlow,
    Direction,
    ExecutionMode,
    IdeaStatus,
    LiquidityRegime,
    OrderIntent,
    PackageSize,
    QualityFlag,
    QualityStatus,
    RiskProfile,
    SkipReason,
    Strategy,
    SwingState,
    Timeframe,
    TrendRegime,
    Venue,
    VolatilityRegime,
)
from .ideas import IdeaEvent, IdeaOutcome, IdeaSkip, TradeIdea
from .market import Bar, DataQualityEvent, Instrument, MarketFeature, RegimeSnapshot
from .portfolio import (
    Account,
    Holding,
    PortfolioModel,
    PortfolioWeight,
    RebalanceDraft,
)
from .risk import AuditEvent, BacktestRun, ModelRegistry, RiskSnapshot, RiskState

__all__ = [
    "Base",
    # market
    "Instrument",
    "Bar",
    "MarketFeature",
    "RegimeSnapshot",
    "DataQualityEvent",
    # ideas
    "TradeIdea",
    "IdeaEvent",
    "IdeaOutcome",
    "IdeaSkip",
    # portfolio
    "Account",
    "Holding",
    "PortfolioModel",
    "PortfolioWeight",
    "RebalanceDraft",
    # risk / research
    "RiskSnapshot",
    "RiskState",
    "AuditEvent",
    "BacktestRun",
    "ModelRegistry",
    # enums
    "AssetClass",
    "BarrierOutcome",
    "DerivativesFlow",
    "Direction",
    "ExecutionMode",
    "IdeaStatus",
    "LiquidityRegime",
    "OrderIntent",
    "PackageSize",
    "QualityFlag",
    "QualityStatus",
    "RiskProfile",
    "SkipReason",
    "Strategy",
    "SwingState",
    "Timeframe",
    "TrendRegime",
    "Venue",
    "VolatilityRegime",
]
