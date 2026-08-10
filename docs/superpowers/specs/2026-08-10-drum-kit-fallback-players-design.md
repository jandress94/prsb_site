# Drum Kit Fallback Players

## Goal

Automatically cover Drum Set when a song’s listed kit player is not available at a gig, using a configurable priority-ordered cover pool — so operators do not need per-song `ASSIGN` overrides for the usual Ben → Jansen → Carlos pattern.

## Context

Today:

- Drum Set is assigned like any other instrument via the gig part-assignment MILP in `prsb/scripts/gig_part_assignment.py`
- Candidates come only from real `PartAssignment` rows among `AVAILABLE` attendees
- Repertoire splits Drum Set songs across Ben, Jansen, and Carlos; when the listed player is out, Engine Room often shows as missing even though another kit player can sit in
- The current workaround is many `GigPartAssignmentOverride` `ASSIGN` rows

Coverage-risk reporting already treats kit/Engine Room specially for catalog fragility; the gig solver does not.

## Success criteria

- Configurable cover pool (member + priority) managed in Django admin and seed data
- Listed Drum Set player always used when they are `AVAILABLE` at the gig
- When no listed Drum Set player is `AVAILABLE`, inject cover-pool candidates at the best available priority tier
- Jansen (priority 1) preferred over Ben/Carlos (priority 2); equal priority left to the existing MILP (song-count, scarcity, random)
- `NOT_PLAYING` / `ASSIGN` overrides still win; `NOT_PLAYING` on the preferred cover falls through to the next priority tier
- No hardcoded instrument or song-part IDs or name strings in solver logic
- `maybe_available` listed players do not count as present (covers kick in); cover-pool members must themselves be `AVAILABLE` to be injected

## Data model

### `Instrument.is_drum_kit_cover_instrument`

- `BooleanField(default=False)`
- Marks which instrument the fallback feature targets (seed/admin: `True` on Drum Set)
- Solver resolves “the kit instrument” by this flag, not by PK or `"Drum Set"` string
- At most one instrument should be flagged; if multiple are flagged, treat as a configuration error in tests/admin guidance (v1: use the first ordered match and document the expectation of a single flag)

### `DrumKitCoverPlayer`

- `member` → `BandMember` (unique)
- `priority` → positive integer; **lower = preferred** (Jansen `1`, Ben `2`, Carlos `2`)
- Managed in Django admin only (no member-facing UI in v1)
- Seed the known trio with those priorities
- Knowing one or two Drum Set songs does **not** put someone in the pool — membership is explicit

## Solver behavior

Hook: candidate-pool construction for each song inside `get_gig_part_assignments` / before `get_gig_song_part_assignments`, only when the gig includes the flagged kit instrument.

Per song:

1. **Listed kit players** = attending (`AVAILABLE`) members with a real `PartAssignment` on `(song_part, kit_instrument)` that is not `NOT_READY` and not blocked by `NOT_PLAYING` for that tuple.
2. If **any** listed kit player remains → do **not** inject covers (primary always wins).
3. If **none** → determine the attachment `SongPart` from existing repertoire `PartAssignment`s on this song for the kit instrument (whatever part those rows use — Engine Room today). If the song has no kit-instrument assignments at all → no injection.
4. Build the injectable cover set:
   - Attending `DrumKitCoverPlayer` members
   - Not already a listed kit candidate on this song
   - Not blocked by `NOT_PLAYING` on `(member, attachment_part, kit_instrument)`
   - Respect existing `ASSIGN`-override song rules (a member with an `ASSIGN` on the song already has their normal pool rows removed; do not inject a cover that would fight a forced non-kit assignment in a confusing way — if the member has an `ASSIGN` override on this song, skip injecting them as a kit cover)
5. Keep only members at the **best remaining priority** (minimum `priority` among eligible covers).
6. Inject unsaved `PartAssignment` instances: `member`, attachment `SongPart`, kit `Instrument`, `performance_readiness=READY`, `can_solo=False`.
7. Existing MILP assigns as usual (one part per member, instrument quantity, missing-part penalty, etc.).

### Attendance

| Status | Listed kit player counts as present? | Eligible as injected cover? |
|--------|--------------------------------------|-------------------------------|
| `available` | Yes | Yes |
| `maybe_available` | No (covers kick in) | No |
| `unavailable` / missing | No | No |

### Override interactions

- **`ASSIGN`**: still forced; overrides always win over automatic covers
- **`NOT_PLAYING` on preferred cover’s kit seat**: exclude that member when selecting the priority tier, so the next tier (e.g. Carlos) is injected
- Manual kit `ASSIGN` overrides remain available as an escape hatch; this feature should remove the need for bulk “Ben is out → Jansen on every Ben song” overrides

### Example (gig 40, song 67 “Rant and Rave”)

- Only Ben has Engine Room / Drum Set; Jansen has Lead only
- Ben `unavailable`; Jansen and Carlos `available`
- No listed kit player available → inject Jansen only (priority 1)
- Synthetic assignment attaches to Engine Room / Drum Set even though Jansen has no Engine Room repertoire row
- If Jansen also has `NOT_PLAYING` on kit → inject Carlos instead

## UI and admin

- Register `DrumKitCoverPlayer` in Django admin (list: member, priority; filter/order by priority)
- `Instrument` admin: expose `is_drum_kit_cover_instrument`
- No band-site UI in v1
- Remove the README Ideas Log bullet for “Handling default or fallback drum kit players” once implemented (plan step)

## Out of scope

- General multi-instrument cover pools
- Member-facing cover-pool settings page
- Soft/`maybe_available` attendance weights in the solver
- Changing coverage-risk semantics
- Moving the solver out of `scripts/` (separate idea-log item)

## Testing

Unit tests (in-memory SQLite) covering:

- Listed drummer `AVAILABLE` → no cover injection; listed player used
- Listed drummer not `AVAILABLE` → best-priority attending cover injected and assignable (cover has no Engine Room repertoire row)
- Priority: Jansen preferred over Carlos when both available and Ben out
- Jansen `NOT_PLAYING` on kit → Carlos injected
- `ASSIGN` override still forces the chosen member/part
- Cover who is only `maybe_available` is not injected
- Song with no kit-instrument `PartAssignment`s → no injection
- Gig without the flagged kit instrument → no injection
