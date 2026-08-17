# FORTS Radar implementation plan

1. Add server contract tests for the fixed six-family response, rejection reason, admitted/no-setup state, active setup and PAPER lifecycle.
2. Extend `server/app/api/v1/diagnostics.py` with a read-only `/forts-radar` endpoint. Reuse persisted `Instrument.metadata_json['admission']`, snapshot metadata, latest non-terminal non-expired `TradeIdea`, and latest `PaperTrade`. Do not call scanner/admission functions.
3. Extend thin-client `lib/ui/screens/server_data_screen.dart` to fetch `/api/v1/diagnostics/forts-radar` alongside market status and render the six families with expandable details.
4. Add a widget-level contract test for null-safe Radar rendering.
5. Run server targeted tests, Flutter targeted tests, then repository Quality Gate. Fix only failures caused by this diff.
6. Open PR to `claude/release-y40hk5`. Merge only after green checks. Because this is an owner-visible production observability milestone, merge with `[final-release]` and verify cumulative VPS deploy and signed sideload build at the exact merge SHA.