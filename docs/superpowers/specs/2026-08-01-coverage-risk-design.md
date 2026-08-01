# Coverage Risk Report

## Goal

Surface which rotation songs and parts are most at risk of not being performable at a typical future gig — independent of any specific gig — so the band can prioritize rehearsal and training. Rank songs by fragility, with drill-down into parts, current coverers, and members who have no assignment on that song yet.

## Context

The app already tracks:

- `PartAssignment` — who can play which song part on which instrument, with `ready` / `backup` / `not_ready`
- `GigAttendance` — available / maybe / unavailable per gig
- Per-gig optimizer gaps (`unplayed_parts`, backup flags)

What is missing is a **catalog-level** fragility view that weights coverers by how reliably they actually attend full-band gigs.

## Success criteria

- Rank rotation songs by coverage risk (worst part drives the song)
- Drill into each song’s parts with effective coverage and risk
- For each part: list coverers (readiness, attendance rate, contribution) and members with no assignment on that song
- Situational awareness + rehearsal focus; no auto-suggested learners in v1

## Scoring model

### Gig flag

Add `Gig.is_small_group` (`BooleanField`, default `False`). Full-band is the default. Small-group gigs are excluded from attendance lookback. Editable on gig create/update (and admin).

### Lookback

Default: last **N = 10** past full-band gigs (`is_small_group=False` and `start_datetime <= now`), ordered by `start_datetime` descending. Configurable via query param on the report page. Only N-gigs lookback in v1 (no months-based window). Future gigs are excluded even if attendance is already recorded.

### Attendance rate

For each member:

```
attendance_rate = (# of lookback gigs marked available) / D
```

where `D` = number of full-band gigs actually found in the lookback (`min(N, available)`).

- Only `available` counts as showed up
- No response, `maybe_available`, and `unavailable` all count against the rate (not in the numerator)
- Small-group gigs never enter the lookback set
- If **zero** full-band gigs exist: `attendance_rate = 1.0` for everyone (rank on coverer count and readiness only)

### Part effective coverage

Over `PartAssignment`s that are `ready` or `backup` (`not_ready` ignored).

A member may have multiple instrument rows on the **same** part. Count **at most one contribution per (member, song_part)**, using that member’s **best** readiness weight on that part (`ready` beats `backup`).

If the same member covers **different** parts on a song, they contribute to **each** of those parts normally — the one-per-part rule does not collapse across parts.

```
contribution = readiness_weight × attendance_rate
```

| Readiness | Weight |
|---|---|
| ready | 1.0 |
| backup | 0.5 |

```
effective_coverage = sum(contributions)
part_risk = 1 / (effective_coverage + ε)   # ε = 0.01
```

Parts with no ready/backup coverers rank highest.

Coverer display lists each qualifying (member, instrument) assignment for transparency, but scoring still uses one contribution per member as above.

### Song risk

```
song_risk = max(part_risk across the song’s parts)
```

Only songs with `in_gig_rotation=True`. Ranking uses worst-part risk.

### Per-part display context

1. **Coverers** — member, readiness, attendance %, contribution  
2. **No assignment on song** — members with zero `PartAssignment` rows on that song (any part/instrument)

## UI

### Navigation

- Home gains a **Reports** link
- `/reports/` — hub page listing analyses (Coverage Risk first; room for more later)
- Coverage Risk is **not** a top-level Home sibling beyond the Reports entry

### Coverage Risk page

Path: `/reports/coverage-risk/?n=10`

1. Title + short note on scoring (lookback N, ready/backup weights)
2. Lookback control: last N full-band gigs (default 10)
3. Expandable song list, highest risk first  
   - Collapsed row: song title, song risk, worst part name + effective coverage  
   - Expanded: each part (worst first) with coverer table and unassigned-on-song list

### Gig forms

Expose `is_small_group` on gig create/update so small-group gigs can be excluded going forward.

## Architecture

### Scoring module

Pure helper (e.g. `prsb/band/coverage_risk.py` or `prsb/scripts/coverage_risk.py`) that:

1. Loads last N full-band gigs
2. Computes attendance rates
3. Scores rotation song parts and songs
4. Attaches coverers and unassigned-on-song members

Scores are **computed on request** — no new tables for risk scores.

### Views / URLs

| Path | Purpose |
|---|---|
| `reports/` | Hub template |
| `reports/coverage-risk/` | Coverage risk report (`n` query param; invalid/missing → 10) |

Auth: same as other band pages (login required).

## Out of scope (v1)

- Per-gig / setlist-specific risk
- Months-based lookback
- Auto-suggesting who should learn a part
- Treating `maybe_available` as partial attendance
- Instrument scarcity in catalog risk

## Testing

### Unit tests (scoring module)

- Attendance: available / D; small-group excluded; zero gigs → rate 1.0
- Part coverage: ready vs backup weights; `not_ready` ignored; no coverers → highest risk; one contribution per member per part (best readiness)
- Song risk = worst part; rotation-only; future gigs excluded from lookback
- Unassigned-on-song excludes anyone with any assignment on that song

### View tests

- Reports hub and Coverage Risk load for logged-in users
- `n` query param; invalid/missing → default 10
- Gig create/update persists `is_small_group`

No browser expand/collapse or performance tests in v1.
