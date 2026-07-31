"""Перечисления домена.

Значения — строки: их видно в базе глазами, они переживают миграции и
попадают в API без перевода. Каждое множество взято из ТЗ дословно, чтобы
расхождение с документом было видно в diff, а не всплывало на сделке.
"""

from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    """Классы инструментов (engine-ТЗ §5.1, §1.1)."""

    MONEY_MARKET = "MONEY_MARKET"
    BOND_FUND = "BOND_FUND"
    OFZ = "OFZ"
    CORPORATE_BOND = "CORPORATE_BOND"
    EQUITY = "EQUITY"
    GOLD = "GOLD"
    COMMODITY = "COMMODITY"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    CRYPTO_PERPETUAL = "CRYPTO_PERPETUAL"
    FUTURES = "FUTURES"
    OPTION = "OPTION"
    INDEX = "INDEX"


class Venue(StrEnum):
    MOEX = "MOEX"
    CRYPTO = "CRYPTO"


class Timeframe(StrEnum):
    """Таймфреймы §7. 1M — только макроуровни, торговых решений не принимает."""

    M15 = "15m"
    M10 = "10m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1M"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class Strategy(StrEnum):
    """Три стратегии engine-ТЗ §10–§12. Четвёртой не предусмотрено."""

    TREND_PULLBACK = "TREND_PULLBACK"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    WYCKOFF_REVERSAL = "WYCKOFF_REVERSAL"


class IdeaStatus(StrEnum):
    """Жизненный цикл идеи (engine-ТЗ §18), дословно и в том же порядке."""

    DISCOVERED = "DISCOVERED"
    WATCH = "WATCH"
    TRIGGERED = "TRIGGERED"
    ACTIVE = "ACTIVE"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    TP1_HIT = "TP1_HIT"
    MANAGING = "MANAGING"
    TP2_HIT = "TP2_HIT"
    STOPPED = "STOPPED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    MISSED = "MISSED"
    RISK_BLOCKED = "RISK_BLOCKED"
    DATA_BLOCKED = "DATA_BLOCKED"
    CLOSED = "CLOSED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL

    @property
    def has_position(self) -> bool:
        return self in _WITH_POSITION


_TERMINAL: frozenset[IdeaStatus] = frozenset(
    {
        IdeaStatus.STOPPED,
        IdeaStatus.TIMED_OUT,
        IdeaStatus.CANCELLED,
        IdeaStatus.MISSED,
        IdeaStatus.CLOSED,
    }
)

_WITH_POSITION: frozenset[IdeaStatus] = frozenset(
    {
        IdeaStatus.PARTIALLY_FILLED,
        IdeaStatus.FILLED,
        IdeaStatus.TP1_HIT,
        IdeaStatus.MANAGING,
        IdeaStatus.TP2_HIT,
    }
)


class QualityStatus(StrEnum):
    """Итог допуска идеи (engine-ТЗ §15.6)."""

    ACTIVE = "ACTIVE"
    WATCH = "WATCH"
    REJECTED = "REJECTED"


class TrendRegime(StrEnum):
    """§9.1. TRANSITION — честное «непонятно», а не «слабый тренд»."""

    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE = "RANGE"
    TRANSITION = "TRANSITION"


class VolatilityRegime(StrEnum):
    """§9.2, перцентиль ATR/price за 252 периода."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class LiquidityRegime(StrEnum):
    """§9.3. UNTRADEABLE запрещает вход, а не понижает балл."""

    GOOD = "GOOD"
    NORMAL = "NORMAL"
    THIN = "THIN"
    UNTRADEABLE = "UNTRADEABLE"


class DerivativesFlow(StrEnum):
    """§9.4, квадранты цена/OI."""

    LONG_BUILDUP = "LONG_BUILDUP"
    SHORT_BUILDUP = "SHORT_BUILDUP"
    SHORT_COVERING = "SHORT_COVERING"
    LONG_LIQUIDATION = "LONG_LIQUIDATION"
    NEUTRAL = "NEUTRAL"


class SwingState(StrEnum):
    """§8.2. Эллиотт только как descriptive state, без нумерации 1–5."""

    IMPULSE_EARLY = "IMPULSE_EARLY"
    IMPULSE_MATURE = "IMPULSE_MATURE"
    CORRECTION = "CORRECTION"
    EXHAUSTION_CANDIDATE = "EXHAUSTION_CANDIDATE"
    AMBIGUOUS = "AMBIGUOUS"


class QualityFlag(StrEnum):
    """Флаги качества данных (UX-ТЗ приложение B, engine-ТЗ §4.3)."""

    STALE = "STALE"
    GAP = "GAP"
    OUTLIER = "OUTLIER"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    PARTIAL_BAR = "PARTIAL_BAR"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    OI_UNAVAILABLE = "OI_UNAVAILABLE"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    CORPORATE_ACTION_UNADJUSTED = "CORPORATE_ACTION_UNADJUSTED"


class OrderIntent(StrEnum):
    """Тип входа (§10.4). Рынок запрещён при плохом спреде."""

    LIMIT_RETEST = "LIMIT_RETEST"
    STOP_CONFIRMATION = "STOP_CONFIRMATION"
    MARKET = "MARKET"


class BarrierOutcome(StrEnum):
    """Метка тройного барьера (§15.2)."""

    WIN = "WIN"
    LOSS = "LOSS"
    TIMEOUT = "TIMEOUT"
    AMBIGUOUS = "AMBIGUOUS"


class ExecutionMode(StrEnum):
    """§21. MVP работает в PAPER."""

    ANALYTICS_ONLY = "ANALYTICS_ONLY"
    PAPER = "PAPER"
    LIVE_CONFIRM_EACH = "LIVE_CONFIRM_EACH"
    LIVE_AUTO = "LIVE_AUTO"


class RiskProfile(StrEnum):
    """Профили портфеля §6.1.

    ``INCOME`` — не четвёртая градация риска, а другой вопрос. Остальные три
    отвечают «сколько колебаний я готов перенести ради роста»; этот —
    «куда положить деньги, чтобы они не лежали». Он существует потому, что
    падающий рынок акций может длиться годами, а денежный рынок и фонды
    облигаций приносят выше инфляции всё это время. Портфель, который в
    такие годы показывает пустой экран, отвечает не на тот вопрос.
    """

    INCOME = "INCOME"
    CONSERVATIVE = "CONSERVATIVE"
    OPTIMAL = "OPTIMAL"
    AGGRESSIVE = "AGGRESSIVE"


class PackageSize(StrEnum):
    """Пакеты §6.5."""

    SIMPLE = "SIMPLE"
    BALANCED = "BALANCED"
    MAX_POTENTIAL = "MAX_POTENTIAL"


class SkipReason(StrEnum):
    """Справочник причин пропуска (UX-ТЗ приложение A)."""

    NOT_SEEN = "NOT_SEEN"
    NO_TRUST = "NO_TRUST"
    RISK_LIMIT = "RISK_LIMIT"
    LATE_ENTRY = "LATE_ENTRY"
    TECHNICAL = "TECHNICAL"
    PSYCHOLOGICAL = "PSYCHOLOGICAL"
    OTHER = "OTHER"
