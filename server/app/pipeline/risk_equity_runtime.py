"""Явный risk equity для scheduler-скана.

Сам `scan()` исторически имел скрытый fallback 100 000 ₽, если вызывающий не
передал RiskState. Scheduler именно так его и вызывает. Для личного приложения
капитал риск-контура уже выбран владельцем и лежит в конфиге; молчаливая
константа в коде больше не должна влиять на число контрактов.

Wrapper ставится до сборки scheduler и касается только default-вызова без
явного RiskState. Тесты/бэктесты, которые передают собственное состояние,
получают прежнее поведение.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..risk.sizing import RiskState
from . import scan as scan_module

_ORIGINAL_SCAN = scan_module.scan


def scan_with_configured_equity(
    session: Session,
    *,
    cfg: EngineConfig | None = None,
    risk_state: RiskState | None = None,
    now: datetime | None = None,
):
    config = cfg or get_config()
    state = risk_state or RiskState(
        risk_equity=config.decimal("risk.equity_rub")
    )
    return _ORIGINAL_SCAN(session, cfg=config, risk_state=state, now=now)


def install() -> None:
    if scan_module.scan is not scan_with_configured_equity:
        scan_module.scan = scan_with_configured_equity
