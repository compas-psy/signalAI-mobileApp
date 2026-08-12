## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Conflict Avoidance

**One delivery line. One author per file. Rebase, don't diverge.**

The rule exists because this repo already paid for breaking it: on 2026-08-06 the
default branch and the active UX branch had diverged 23 and 102 commits from their
common ancestor, so the APK and the VPS could ship different implementations of the
same logic. See `docs/DEVELOPMENT_PROCESS.md`.

Before writing code:
- Branch from the current integration branch, never from an old release/UX branch.
  `git fetch origin <integration-branch>` first - work from what is there now, not
  from a stale local copy.
- Check what else is in flight (open PRs, other sessions). If your task overlaps a
  file someone else owns, work in another file or wait for the handoff. Two agents
  never edit the same code in parallel.
- Keep the change small and vertical. One PR - one coherent result. Long-lived
  branches are how divergence starts.

While working:
- Rebase onto the integration branch before you push, not after the conflict appears.
- Don't reformat, re-sort imports, or re-wrap lines you didn't otherwise change -
  cosmetic diffs turn a clean merge into a manual one (see §3).
- Prefer adding a new function/file over rewriting a shared one when both satisfy
  the request equally.

Files that conflict by nature - handle them, don't hand-merge them:
- `pubspec.lock`, `server/uv.lock` - never edit or resolve by hand. Take the
  integration branch's version, then regenerate (`flutter pub get`,
  `uv lock`) and verify CI's lock check passes.
- `server/alembic/versions/*` - the graph must keep a single head. After rebasing,
  re-point your revision's `down_revision` at the new head and renumber the file to
  the next free prefix. Don't create a merge revision to paper over two heads.
- `CHANGELOG.md`, `HANDOFF.md` - append your entry, keep both sides' entries, don't
  rewrite or reorder existing ones.
- Generated and build outputs - regenerate from source, never resolve textually.

When a conflict does happen:
- Read both sides before choosing. Never resolve wholesale with `--ours` / `--theirs`.
- Keep both intents when both are still wanted; if they genuinely contradict, stop
  and ask - a silently dropped change is worse than a blocked merge.
- After resolving, re-run the checks that cover the touched side: `flutter analyze`,
  `flutter test`, server `pytest`, `alembic upgrade head` + `alembic check`.
- Never force-push a shared branch and never push straight to the integration branch
  to dodge a red `Quality gate`.

The test: after your push, anyone can fast-forward the integration branch and get
exactly the behavior you verified.
