# PRSB Site

## Dev server

Load `env_vars/dev.env` and start the dev server:

```bash
set -a && source env_vars/dev.env && set +a && cd prsb && poetry run python manage.py runserver
```

First time (or after migrations):

```bash
set -a && source env_vars/dev.env && set +a && cd prsb && poetry run python manage.py migrate && poetry run python manage.py runserver
```

## Tests

Unit tests use an in-memory SQLite database (no Postgres required):

```bash
cd prsb && poetry run python manage.py test
```

To run only the band app tests:

```bash
cd prsb && poetry run python manage.py test band.tests
```

## Ideas Log

These are ideas for new features or changes to existing functionality.
Items marked `[agent]` were added from prior conversation themes and a project scan.
- Indication of missing soloist on a song that requires it
- UI to add and edit song parts directly from the song creation or edit form
- Authentication and RBAC
- addition of .../me (or something like that) to allow authenticated users direct access to their own profile and song parts
- Member "About Me"
- Multiple bands
- Caching / saving gig part assignments for better latency
- Something better for the part-assignments page. Loading everything is too massive and isn't useful.
- Other reports (t-shirts, birthdays, etc)
- Refactor the gig part assignment scoring / solving logic to make it more clear and understandable.
- move to lambda-based architecture and scale-to-zero DB to minimize cost.
- Instrument management UI (CRUD / coverage-risk and song-count flags without Django admin) `[agent]`
- Edit gig part assignment overrides in place (create/delete only today) `[agent]`
- Delete part assignments from the web UI `[agent]`
- Show setlist / recommendations on the gig part-assignments page in setlist order (print view already can) `[agent]`
- Treat `maybe_available` as soft/partial attendance in the solver and/or coverage risk `[agent]`
- Per-gig / setlist-specific coverage risk (catalog risk exists; gig-scoped does not) `[agent]`
- Months-based lookback option for coverage risk (v1 is last N gigs only) `[agent]`
- Suggest who should learn a high-risk / uncovered part `[agent]`
- Factor instrument scarcity into catalog coverage risk `[agent]`
- Reward filled solos in gig recommendation scoring (solo preference exists; score does not) `[agent]`
- `can_solo` on gig part assignment overrides `[agent]`
- Band special dates UI (birthdays Lambda / `BandSpecialDate` exist; no member-facing page) `[agent]`
- Move solver out of `scripts/` into a clearer `band/services/` (or similar) module boundary `[agent]`
- Setlist editor: allow adding and reordering a new entry in one save `[agent]`
- Highlight when max instrument usage exceeds available quantity on the gig page `[agent]`
- Member attendance / reliability report (rates already computed for coverage risk) `[agent]`
