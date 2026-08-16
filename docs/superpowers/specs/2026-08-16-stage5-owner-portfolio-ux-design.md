# Stage 5 Owner Portfolio UX — Design

## Goal
Replace the mobile owner-facing 3×3 profile/package selection with exactly three investment choices provided by the server: «Консервативный», «Сбалансированный», «Доходный».

## Architecture
The server remains the source of truth for owner-facing portfolio selection through `GET /api/v1/portfolio/headlines?horizon_years=N`. Flutter parses that response into a small headlines domain model and renders the three slots directly. The existing `/portfolio/packages` contract remains available for technical package detail and rebalance compatibility, but the owner screen must not derive its choices from that matrix.

## UX
- Keep the existing strict horizon selector above the portfolio content.
- Render exactly three owner-facing cards in server order.
- `ready`: show the selected package summary and allow detail/rebalance actions.
- `riskier_than_target`: show the package, but with an explicit risk warning and server reason.
- `missing`: keep the slot visible with the server reason and a retry action; never silently substitute another profile or horizon.
- Card summary: expected return band, risk/drawdown, mix, and short rationale when a package exists.

## Data flow
`PortfolioScreen` → controller headlines loader → `EngineClient.portfolioHeadlines(horizonYears)` → `/api/v1/portfolio/headlines` → `PortfolioHeadlines` domain model → owner cards.

Changing horizon invalidates/reloads the headlines for that exact horizon. No mobile fallback selects another horizon or internal package variant.

## Error handling
A network/server failure is represented explicitly as unavailable state. A successful response with a missing portfolio is not an error and remains a visible `missing` slot.

## Testing
1. Client contract test verifies exact endpoint/horizon and preserves `ready`, `riskier_than_target`, `missing`.
2. Domain parsing tests verify exactly three ordered slots and package parsing.
3. Widget/controller tests verify three visible owner choices and horizon-driven reload.
4. Full Quality Gate: Flutter analyze/tests plus unchanged server/migration/secret/release checks.
