"""Issuer identity for portfolio concentration must come from MOEX, never names."""

import numpy as np
from sqlalchemy import select

from app.market import moex
from app.market.investments import sync_investments
from app.models import Instrument
from app.models.enums import AssetClass, RiskProfile
from app.portfolio.build import PROFILES, _constraints
from app.portfolio.stats import project_with_groups


def _report(url: str):
    return moex.FetchReport(url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True)


def test_moex_security_issuer_id_reads_explicit_emitter_id():
    def fetch(url: str):
        return {
            "description": {
                "columns": ["name", "value"],
                "data": [
                    ["NAME", "Сбербанк России ПАО ао"],
                    ["EMITTER_ID", "484"],
                ],
            }
        }, _report(url)

    issuer_id, _ = moex.security_issuer_id("SBER", fetch=fetch)
    assert issuer_id == "MOEX:EMITTER:484"


def test_moex_security_issuer_id_does_not_guess_when_source_has_no_id():
    def fetch(url: str):
        return {
            "description": {
                "columns": ["name", "value"],
                "data": [["NAME", "Сбербанк России ПАО ао"]],
            }
        }, _report(url)

    issuer_id, _ = moex.security_issuer_id("SBER", fetch=fetch)
    assert issuer_id is None


def test_sync_persists_explicit_issuer_id_only(session):
    securities = {
        "columns": ["SECID", "SHORTNAME", "MINSTEP", "DECIMALS", "LOTSIZE",
                    "ISSUESIZE", "SECTYPE"],
        "data": [["SBER", "Сбербанк", 0.01, 2, 10, 21586948000, "1"]],
    }
    marketdata = {
        "columns": ["SECID", "LAST", "VALTODAY"],
        "data": [["SBER", 312.4, 9_800_000_000]],
    }

    def fetch(url: str):
        if "/securities/SBER.json" in url:
            return {
                "description": {
                    "columns": ["name", "value"],
                    "data": [["EMITTER_ID", "484"]],
                }
            }, _report(url)
        if "boards/TQBR" in url:
            if "start=0" not in url:
                return {"securities": [], "marketdata": []}, _report(url)
            return {"securities": securities, "marketdata": marketdata}, _report(url)
        return {"securities": {"columns": [], "data": []},
                "marketdata": {"columns": [], "data": []}}, _report(url)

    sync_investments(session, fetch=fetch)
    session.flush()
    instrument = session.execute(
        select(Instrument).where(Instrument.instrument_id == "MOEX:EQ:SBER")
    ).scalar_one()
    assert instrument.metadata_json["issuer_id"] == "MOEX:EMITTER:484"


def _project(issuer_ids: list[str | None]) -> np.ndarray:
    classes = [AssetClass.EQUITY, AssetClass.EQUITY, AssetClass.MONEY_MARKET]
    constraints = _constraints(
        classes,
        PROFILES[RiskProfile.OPTIMAL],
        positions=5,
        issuer_ids=issuer_ids,
    )
    return project_with_groups(
        np.array([0.55, 0.35, 0.10]),
        constraints.lo,
        constraints.hi,
        constraints.groups,
        iterations=200,
    )


def test_same_issuer_securities_share_single_name_cap():
    weights = _project(["MOEX:EMITTER:484", "MOEX:EMITTER:484", None])
    # positions=5 => existing single-name ceiling is 30%; two share classes
    # of one issuer must not turn that into 60% issuer exposure.
    assert float(weights[:2].sum()) <= 0.300001
    assert abs(float(weights.sum()) - 1.0) <= 1e-8


def test_distinct_issuers_remain_independent():
    weights = _project(["MOEX:EMITTER:111", "MOEX:EMITTER:222", None])
    assert float(weights[:2].sum()) > 0.300001
    assert abs(float(weights.sum()) - 1.0) <= 1e-8


def test_missing_issuer_identity_is_never_guessed_into_shared_group():
    weights = _project([None, None, None])
    assert float(weights[:2].sum()) > 0.300001
