# Solo-Aware Gig Part Assignment

## Goal

Capture which song parts have solos and which members can take those solos, then prefer solo-capable members when assigning gig parts — without changing song recommendation ranking.

## Context

Today:

- `SongPart` describes named parts on a song
- `PartAssignment` records that a member knows a part on an instrument, with readiness
- The gig part-assignment MILP covers parts first, then prefers scarce instruments; displayed song scores use missing-part bands plus instrument fit

What is missing is repertoire knowledge about solos and a soft preference to put soloists on solo parts.

## Success criteria

- Mark parts that include a solo (`SongPart.has_solo`)
- Mark which part assignments can take that solo (`PartAssignment.can_solo`), only when the part has a solo
- When assigning a solo part, prefer a ready member who `can_solo` over one who cannot
- Still cover the part with a non-soloist if no soloist is available
- Solo preference beats instrument scarcity for that choice; covering any part still beats solo preference
- Song recommendation scores remain missing-parts + instrument fit only
- Edit `has_solo` / `can_solo` in Django admin and on the band site

## Data model

### `SongPart.has_solo`

- `BooleanField(default=False)`
- Means this part includes a solo
- No song-level flag; a song “has a solo” iff any of its parts has `has_solo=True` (derived when needed)

### `PartAssignment.can_solo`

- `BooleanField(default=False)`
- Means this member can take the solo when playing this part on this instrument
- Allowed only when `song_part.has_solo` is true
- Enforced in model/form validation (reject `can_solo=True` on a non-solo part)
- Existing rows default to `False` (no backfill)

### Consistency when `has_solo` is cleared

When `SongPart.has_solo` is saved as `False`, clear `can_solo` on all related `PartAssignment` rows in that same save path so stored data stays consistent.

## Assignment optimizer

Extend `get_gig_song_part_assignments` objective coefficients:

1. **Missing-part penalty** — unchanged; still dominates
2. **Soloist bonus** — for each ordinary `PartAssignment` decision variable where `song_part.has_solo` and `can_solo` are both true, add a negative objective coefficient (reward). Magnitude: larger than the maximum instrument-scarcity reward available for the song, and smaller than one missing-part penalty
3. **Instrument scarcity reward** — unchanged magnitude relative to today, but dominated by the soloist bonus
4. **Per-song / random tie-breakers** — unchanged

Ordering of priorities:

```
cover part ≫ assign soloist on solo part ≫ scarce instrument fit ≫ song-count / random
```

Behavior:

- Soft preference only: non-soloists remain eligible and will cover the part if no soloist can
- Displayed score from `get_score(num_missing_parts, instrument_fit)` unchanged; solo fields do not enter song ranking

### Override note (v1)

`GigPartAssignmentOverride` ASSIGN rows force a person onto a part as today and do not gain a `can_solo` field. Override decision variables receive no soloist bonus (they are already forced). Solo preference applies only to ordinary `PartAssignment` candidates.

## UI and admin

### Admin

- Song → SongPart inline: `has_solo` checkbox
- PartAssignment admin: `can_solo` field (optional list column / filter)

### Site — songs

- Song update: add a parts section for existing parts with a `has_solo` checkbox per part
- Part create/order remains admin-only in v1 (no full on-site part CRUD)
- Song detail: indicate which parts have a solo

### Site — part assignments

- Create/update forms include `can_solo`
- Checkbox enabled only when the selected song part has `has_solo`; otherwise disabled/hidden and forced `False`
- Server-side `clean()` rejects invalid `can_solo=True`
- Song detail / lists: indicate assignments that can solo

### Site — gig part assignments

- Light markers for solo parts and/or soloist assignees so preference is visible
- Recommendation score display unchanged

## Testing

1. Validation rejects `can_solo=True` when the part has no solo; accepts when it does
2. Solo part with two ready knowers (one `can_solo`, one not) → soloist assigned
3. Solo part with only non-soloists → part still covered
4. Solo preference beats instrument scarcity for that part; missing-part coverage still dominates
5. `get_score` / song ranking criteria unchanged by solo fields
6. Clearing `has_solo` clears related `can_solo` flags

## Out of scope (v1)

- Song-level `has_solo` flag (use derived `any(part.has_solo)`)
- Separate solo-capability table
- Changing recommendation score to reward filled solos
- Hard requirement that solo parts only be played by soloists
- `can_solo` on gig overrides
- Full on-site SongPart CRUD / reordering
