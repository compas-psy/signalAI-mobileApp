"""Issuer identity for portfolio concentration must come from MOEX, never names."""

from decimal import Decimal

from sqlalchemy import select

from app.market import moex
from app.market.investments import sync_investments
from app.models import Instrument


def _report(url: str):
    return moex.FetchReport(url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True)


def test_moex_security_issuer_id_reads_explicit_emitent_inn():
    def fetch(url: str):
        return {
            "description": {
                "columns": ["name", "value"],
                "data": [
                    ["name", "Сбербанк"],
                    ["emitent_inn", "7707083893"],
                ],
            }
        }, _report(url)

    issuer_id, _ = moex.security_issuer_id("SBER", fetch=fetch)
    assert issuer_id == "MOEX:INN:7707083893"


def test_moex_security_issuer_id_does_not_guess_when_source_has_no_id():
    def fetch(url: str):
        return {
            "description": {
                "columns": ["name", "value"],
                "data": [["name", "Сбербанк"]],
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
                    "data": [["emitent_inn", "7707083893"]],
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
    assert instrument.metadata_json["issuer_id"] == "MOEX:INN:7707083893"
