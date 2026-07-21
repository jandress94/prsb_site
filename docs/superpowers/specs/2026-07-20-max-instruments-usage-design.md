# Max Instruments Used on Gig Part Assignments Page

## Goal

On the gig part-assignments page (`/gigs/<pk>/gig-part-assignments/`), add a summary table that shows, for each instrument brought to the gig, the **maximum number of that instrument used on any single song**. This helps decide what to pack: if Congas max is 0, leave them home; if Lead max is 5 and Available is 7, two can stay behind.

## Context

The page already shows:

- Setlist song assignments
- Song recommendations (proposed assignments for other rotation songs)
- **Number of Songs per Person** (scoped to setlist when present, otherwise all proposed songs)

Instrument usage is already computed per song inside each `GigPartAssignment` (via `part_assignments` and `unplayed_instruments`). This feature aggregates that into a peak-across-songs view.

## UI

Place a new table directly under **Number of Songs per Person**, stacked (same layout pattern as the rest of the page).

**Title:** Max Instruments Used

| Instrument | Max Used | Available |
|---|---|---|
| Lead | 5 | 7 |
| Double Tenor | 1 | 1 |
| Congas | 0 | 1 |

Requirements:

- Sortable table, same styling as songs-per-person (`class="sortable"`)
- One row per `GigInstrument` for the gig, **including max 0**
- Order rows by `Instrument.order`
- Columns: Instrument name, Max Used (int), Available (`GigInstrument.gig_quantity`)
- No shortfall highlighting in v1; Available is enough to compare by eye

## Scope of songs

Mirrors songs-per-person:

- If the setlist has at least one song with assignments → max across **setlist** songs only
- Otherwise → max across **recommendation / proposed** song assignments
- If both lists are empty → every instrument shows Max Used = 0 with its Available
- Setlist breaks (entries with no song) are already excluded from assignment lists

## Computation

Add a pure helper in `prsb/scripts/gig_part_assignment.py`:

```python
def get_max_instrument_usage(
    gig_part_assignments: list[GigPartAssignment],
    gig_instruments: list[GigInstrument],
) -> list[tuple[Instrument, int, int]]:
    ...
```

Algorithm:

1. For each song’s `GigPartAssignment`, count instruments in `part_assignments` (`Counter(pa.instrument for pa in ...)`).
2. For each `GigInstrument`, take the max count across those songs (default 0 if never used).
3. Return `[(instrument, max_used, gig_quantity), ...]` sorted by `instrument.order`.

Counting rule: “how many players are assigned to this instrument on this song” — peak concurrent need for packing.

## View wiring

In `GigPartAssignmentsDetailView.get_context_data`:

1. Call `get_gig_part_assignments` as today.
2. Choose scoped list: `setlist` if non-empty, else `recs`.
3. Load `GigInstrument` objects for the gig.
4. Set `context['max_instrument_usage'] = get_max_instrument_usage(...)`.

Do **not** change the return signature of `get_gig_part_assignments`.

## Template

In `band/gig_part_assignments.html`, after the songs-per-person table, render the new table looping `max_instrument_usage`.

## Testing

Unit-test `get_max_instrument_usage` (no MILP required):

1. Peak across songs: song A uses 3 Lead, song B uses 5 Lead → max Lead = 5
2. Unused instrument: never assigned → max = 0, still present with its `gig_quantity`
3. Empty assignments: no songs → every instrument max = 0

No new template/integration test required for v1.

## Out of scope

- Color/highlight when Max Used > Available (solver already caps at Available)
- Changing how songs-per-person is scoped
- Print views / by-member views
- Persisting these stats in the database
