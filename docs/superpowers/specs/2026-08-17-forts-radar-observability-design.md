# FORTS Radar observability

## Goal

Make the production FORTS pipeline explainable to the owner without changing scanner or admission behaviour. For the six core families `SI`, `CR`, `GOLD`, `SILV`, `BR`, `NG`, the app must answer whether a current contract is observed, admitted, has an actionable setup, and whether a confirmed PAPER trade is being managed by the server.

## Source of truth

The VPS database is authoritative. The phone does not re-run MOEX admission or infer trade lifecycle. Diagnostics reads existing `Instrument`, `TradeIdea`, `PaperTrade` and persisted admission/snapshot metadata.

## Server contract

Add authenticated read-only `GET /api/v1/diagnostics/forts-radar` returning exactly six roots in stable order.

For each root expose canonical root and label, selected current contract, admission state and primary reason, persisted turnover/OI/spread/H1-history/expiry/freshness, latest current idea, and latest PAPER trade lifecycle with current stop and last reconciliation.

A missing measurement is `null`, never fabricated. An admitted instrument with no current idea is explicitly `ready_no_setup`, not an error and not an artificial signal.

## Contract selection

Resolve by FORTS root metadata rather than hard-coded expiring tickers. Canonical families normalize MOEX roots `SI`, `CR`, `GD/GL`, `SV/S2`, `BR`, `NG`. Prefer current non-expired family members, then in-universe/tradable state, then later expiry/update time.

## Mobile UX

Extend the thin-client `ServerDataScreen` in the existing “Настройки и данные” area. Add a `FORTS Radar` section above manual rechecks. Each family shows contract, stage and primary reason; details show liquidity/history/freshness and PAPER lifecycle. Legacy direct MOEX/Bybit `DiagnosticsScreen` remains unchanged.

## Safety

Do not weaken any admission gate, create ideas, send orders, enable live-money execution, or introduce a device-side market state.

## Acceptance

1. Endpoint always returns six roots, including missing families.
2. Rejected family explains the persisted admission failure.
3. Admitted family without a current setup is distinguishable from missing/broken data.
4. Current setup and PAPER lifecycle are linked to the selected contract.
5. Thin-client renders nullable server diagnostics safely.
6. Existing Quality Gate remains green.