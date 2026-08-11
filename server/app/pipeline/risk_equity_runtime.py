"""Единственная точка включения runtime risk engine в scheduler.

API-процесс риск не пересчитывает: он только подписывает уже выпущенный план.
Scheduler получает явный risk equity владельца и затем устанавливает Risk &
Exit Engine v2, который перехватывает единственный active compute_budget path.
Это специально сделано здесь, а не в ``app.main``: два процесса не должны
независимо считать размер одной и той же сделки.
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

    # Ставится только в scheduler. Внутри install есть fail-fast проверка:
    # compute_budget, probability/confidence и оба paper execution sensor-а
    # обязаны ссылаться ровно на один v2 runtime.
    from ..risk.engine_v2 import assert_single_runtime, install as install_risk_v2
    from ..risk.policy_overlay import install as install_policy_overlay

    install_risk_v2()
    # Champion overlay не создаёт второго risk calculator: он разрешён только
    # для exit geometry новых идей и восстановления уже подписанной policy.
    install_policy_overlay()
    assert_single_runtime()
