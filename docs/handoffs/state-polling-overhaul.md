# Handoff: State polling overhaul (#16 + #15 + maybe #12)

## Project context

- **Repo:** `jj358mhz/csl-slicer-console`
- **Current version:** v1.4.0 (just shipped)
- **Branch state:** on `main`, clean, four PRs merged this session (#14, #11, #1, plus v1.4.0 release)
- **Stack:** Flask 3 + Jinja2 + HTMX + SQLAlchemy 2 + SQLite, deployed via GitHub Actions SSH to Utility Pi at csl.telcomjj.com
- **Test rhythm:** no local pytest — push and let CI run. Ruff is picky about trailing newlines and long lines; expect a fix-up commit or two per feature.

## What to build

Read the issue bodies first:
- https://github.com/jj358mhz/csl-slicer-console/issues/16 (coalesced polling)
- https://github.com/jj358mhz/csl-slicer-console/issues/15 (skip no-op writes)
- https://github.com/jj358mhz/csl-slicer-console/issues/12 (polling toggle)

Summary of the plan:
1. **#16 is the main event.** Replace N per-slicer `GET /slicers/<id>/state` polls with one `GET /accounts/<id>/slicer-states` per account. Returns an HTML fragment containing badge + meta partials for every slicer on that account, using HTMX OOB swaps to update multiple tiles from one response.
2. **#15 folds into #16's new handler.** Before writing to the DB, compare fresh values against the current row; skip commit if `last_state` and `connection_mode` are unchanged. Sketch is in the issue body.
3. **#12 is deferred by its own issue body.** Reread it — if poll counts in Docker logs haven't shown a problem, close as won't-fix.

## Foundations already in place

- **`app/uplynk/states.py`** — `RetrieveState.SLICING` etc are the canonical values to compare against for the change-detection logic in #15. Import from there.
- **`UplynkDiscoveryClient.list_slicers()`** in `app/uplynk/discovery.py` — this is the one-call endpoint #16 uses instead of N `retrieve_slicer()` calls. Already tested, already returns `DiscoveredSlicer` with `state` and `connection_mode` fields populated.
- **`poll_slicer_state()`** in `app/slicers/service.py` — the current per-slicer path. Coalesced version can either replace this or live alongside (routes call one or the other). Personal recommendation: new function `poll_account_slicer_states()`, leave the old one for on-demand single-slicer refresh (issue #16 explicitly allows retiring the single-slicer route OR keeping it).

## Complications to work through

Per the #16 issue body:
- **Access control per user.** A regular user assigned to only some slicers on an account shouldn't get poll data for the ones they're not assigned to. Response must filter per user before rendering. `user.slicers` gives you the assignment set.
- **OOB swap targeting.** Verify HTMX handles 10+ `hx-swap-oob="true"` fragments in one response cleanly. Existing `_state.html` does one OOB swap per response — the coalesced version needs a loop.
- **Cross-account dashboards.** The dashboard currently has one polling element per slicer card. Coalesced polling needs one polling element per **account section** (which is a natural fit since `index.html` already groups by workspace). The polling element replaces the per-card polling; verify all slicer-cards in that section update via OOB swap.
- **What triggers the poll.** Currently each badge has `hx-trigger="every 30s"`. Move that to the section-level element.

## Testing considerations

- **Route:** poll returns fragments only for slicers the current user can control. Admin should get all; regular user should get subset.
- **DB writes:** second poll with identical values does NOT update `last_seen_at` (or does — decide during PR per #15's open question). Confirm existing `test_state_persists_fresh_data` still passes for the change case.
- **API call count:** one `list_slicers()` call per poll regardless of slicer count. Mock and assert.

## Out of scope

- Websocket / SSE push (much bigger change, only if we outgrow polling).
- Configurable poll interval (that's #12).
- Any DB schema change.
- Any change to the SHA1 control path.

## Existing patterns to match

- Service functions raise `SlicerAccessDenied` for auth failures; route catches and returns 403.
- All DB writes go through `db.session.commit()` in the service, not the route.
- HTMX partials live in `app/templates/slicers/` as `_name.html`.
- New tests go in `tests/test_<module>.py`; use `_mk_scene` fixture pattern from `test_slicer_service.py`.
- Commit in atomic units, small commits, `Refs #NN` in intermediate commits and `Closes #NN` in the final.
- User preference: pause between code blocks for testing before printing the next.
