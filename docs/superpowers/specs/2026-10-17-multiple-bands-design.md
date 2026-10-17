# Multiple Bands

## Goal

Let one site hold more than one band. Songs and people are shared. Instruments are shared. A gig usually belongs to one band and can belong to several. On a multi-band gig, each setlist song chooses who may play it.

Any logged-in user can open any band. Membership does not restrict what a user can see. Band-scoped roles stay on the ideas list and are out of this work.

## Context

Today every song, member, gig, drum-kit cover, and special date sits in one implicit band. `Song.in_gig_rotation` is a single flag. The gig solver in `prsb/scripts/gig_part_assignment.py` treats every available attendee as one pool and solves once per distinct song. `Gig.is_small_group` only changes coverage-risk attendance stats.

## Success criteria

- A band has its own roster, repertoire, rotation flags, gigs, drum-kit cover order, and special dates.
- A person has one profile and one set of part knowledge, shared by every band they join.
- A song has one title and one part list, shared by every band that uses it. Rotation is per band.
- A gig is listed under every band on it.
- Each song on a multi-band setlist can require one band, prefer one band, or use any band on the gig. Two entries for the same song can differ.
- Preferring a band fills a hole with another band's player rather than leaving the part empty, and keeps a preferred player when both lineups cover the same parts.
- Deactivating a band, a membership, or a song link does not rewrite gigs, attendance, or setlists that already exist.
- Existing PRSB data lands in one active band named PRSB.

## Data model

### `Band`

- `name`
- `slug`, unique, used in URLs
- `is_active`, default true

There is no delete action in the app. Deactivate retires a band. A Django admin delete is refused while the band is on a gig or named by a setlist entry.

### `BandMembership`

- `band` → `Band`
- `member` → `BandMember`
- `is_active`, default true
- Unique on `(band, member)`

Profile fields stay on `BandMember`: phone, emergency contact, bio, birthday, dietary restrictions, shirt size. Part assignments stay on the person. Adding a person who already has an inactive row reactivates that row.

### `BandSong`

- `band` → `Band`
- `song` → `Song`
- `in_gig_rotation`, default false
- `is_active`, default true
- Unique on `(band, song)`

`Song.in_gig_rotation` is removed. Song title, composer, duration, form, and song parts stay on `Song`. Adding a song that already has an inactive link reactivates that link.

### Unchanged shared records

- `Instrument`, including quantity, ordering, coverage-risk flags, and the drum-kit cover flag
- `PartAssignment` (member, song part, instrument, readiness, solo)
- `Song` and `SongPart`

### Moved onto a band

- `DrumKitCoverPlayer` gains `band`. Unique on `(band, member)` instead of member alone. Priority stays per row. Lower number is preferred.
- `BandSpecialDate` gains `band`.

### `Gig` bands

A gig has a many-to-many with `Band` and must have at least one. The same gig row is shown under every band on it.

### Setlist staffing

Staffing is stored on `GigSetlistEntry`, not on the gig. Breaks leave it empty. A one-band gig leaves it empty on song entries too. A gig with two or more bands stores one mode on each song entry:

- **Only** that band. `staffing_band` is required, must be one of the gig's bands, and the song must be active in that band when the entry is added or changed.
- **Prefer** that band, then fill gaps. Same band and repertoire requirements as Only.
- **Any band on the gig.** `staffing_band` is empty. The song must be active in at least one band on the gig when the entry is added or changed.

A person who has a membership in the preferred band and in another band on the gig counts as preferred.

## Archive rules

Deactivate does not walk old gigs. Setlist entries, attendance, and overrides already stored stay valid. Saving a gig without changing those entries still succeeds, even if a band, membership, or song link has since gone inactive.

The active flag gates new choices:

- The home list and the band switcher show active bands only. An inactive band's URL still opens so its history can be read. New songs, memberships, and gigs cannot be created from an inactive band. Reactivating it returns it to the switcher and allows new records again.
- New gigs and new or changed staffing choices can name an active band only.
- The setlist picker and recommendations offer songs with an active link to a band on the gig.
- The roster and the new-attendance picker offer people with an active membership in a band on the gig.

The solver does not consult `is_active`. It uses the attendance rows stored on the gig. A deactivated person who is already marked available stays eligible, including for a band named on an entry. A band match is any `BandMembership` row for that band, active or inactive.

## URLs and navigation

`/bands/<slug>/` is that band's home. Members, songs, part assignments, gigs, and reports live under it. A gig URL works for every band on that gig and 404s for any other band. Creating a song or a gig from a band starts it linked to that band.

These stay global, with no band in the path: login, logout, password change, profile, and instruments.

`/` lists active bands. When exactly one band is active, `/` redirects to it. When none are active, `/` shows an empty list.

The header on band pages switches among active bands. Any logged-in user can open any band. Old unscoped paths such as `/gigs/` are removed. There is no redirect from them.

## Pages

The member list is the active roster. Opening a person shows the shared profile and the parts they know. Linking a person to a band does not copy them.

The song list is this band's active repertoire and shows this band's rotation flag. Creating a song creates the shared song and an active link here. An existing song can be linked too. Editing the title or parts changes that song for every band. Editing rotation changes only this link. The song page lists the other bands with an active link to it.

The part-assignment list shows assignments on this band's active songs. Editing an assignment changes what that person knows everywhere.

The gig list shows gigs that include this band. The gig form starts with this band checked, can add other active bands, and cannot save with zero bands. Once the gig has two or more bands, each song row has its own staffing control. The attendance page lists the gig's stored attendance and offers new rows for active members of the gig's bands.

Coverage risk uses this band's active roster, its active in-rotation songs, and gigs that include this band. `is_small_group` still excludes a gig from the attendance lookback. The scoring formula is unchanged.

Dietary restrictions use this band's active roster. A selected gig that does not include this band is ignored, and the report falls back to the roster list. The status filters are unchanged.

A multi-band gig opened from one band still shows other bands' members where that song's staffing rule allows them.

Django admin can edit bands, memberships, and song links. The birthday script groups special dates by band and includes the band name in the message.

## Solver

`get_gig_part_assignments` solves each setlist entry on its own, in setlist order, so two copies of a song can use different bands. Workload counts accumulate in that order. Results are one row per setlist entry, not one row per distinct song.

Eligible people are attendees with `GigAttendance.AVAILABLE` who have a membership in at least one band on the gig. `maybe_available` stays unused.

- **One-band gig.** One solve on attendees who belong to that band.
- **Only.** One solve on attendees who belong to the named band.
- **Prefer.** One solve on attendees who belong to any band on the gig. Objective order:
  1. Fewest missing parts. A non-preferred player beats a hole.
  2. Among equally complete lineups, fewest non-preferred players.
  3. Existing readiness, instrument-fit, workload, and tie-break scores.
- **Any band on the gig.** One solve on the combined attendees, with no band penalty.

The non-preferred penalty is smaller than one missing part and larger than anything in step 3, so instrument fit cannot pull in a non-preferred player while a preferred player can cover the part.

A setlist song is solved even when it is out of rotation. Recommendations are songs with an active in-rotation link to at least one band on the gig, excluding songs already on the setlist, solved like **Any band on the gig**.

Drum-kit cover candidates come from the cover lists of the bands in that solve. **Only** uses the named band's list. **Prefer** and **Any** use every band on the gig. A person on two lists keeps their best (lowest) priority. Ties break by band name. In a **Prefer** solve, a cover player who belongs to the preferred band counts as preferred.

A new override must name someone that entry's pool allows. An override already stored survives a later deactivation. The form only offers people the entry is allowed to use.

## Validation

Saves fail with a message. Nothing is deleted in the background to force a save through.

- A gig needs at least one band.
- A one-band gig stores no staffing mode. Dropping back to one band clears modes on that save.
- A song entry added to a multi-band gig, or one whose song, mode, or staffing band changed, must have a valid mode. Entries left untouched stay as stored, even when a later deactivation would fail the checks above.
- **Only** and **Prefer** name a band that is on the gig. That band must be active, and the song must be active in that band.
- **Any band on the gig** leaves `staffing_band` empty. The song must be active in at least one band on the gig.
- Breaks have no mode.
- Taking a band off a gig fails while an entry names that band, or while a setlist song would have no `BandSong` row, active or inactive, on any remaining band.
- Attendance rows for someone who no longer belongs to a remaining band stay stored. The solver ignores them until they belong to a band on the gig again.
- A new override outside that entry's pool fails.
- Deactivating a band, membership, or song link does not by itself make an existing gig invalid.

## Migration

One migration creates an active band named PRSB with slug `prsb`. It links every existing song, copying `in_gig_rotation` onto the link, then removes that field from `Song`. It links every existing member and every existing gig. It attaches existing drum-kit cover players and special dates to PRSB. Every new link starts active. Existing gigs have one band, so their staffing modes stay empty.

## Tests

- An inactive band is absent from the switcher and still opens by URL. A new gig cannot name it, and its pages cannot create songs, memberships, or gigs.
- Rotation is per band. Editing a title changes the shared song. Deactivating a song link hides it from that band's list and from new setlist picks. An old setlist entry still solves.
- A gig is listed under every band on it and 404s under a band that is not on it.
- A one-band gig stores no staffing mode. A multi-band gig requires a mode on each new or changed song entry. Two entries for the same song can name different bands.
- Deactivating a member removes them from the roster and from new attendance picks. Existing attendance still makes them eligible.
- **Only** never assigns another band.
- **Prefer** assigns a non-preferred player rather than leave a hole, and keeps the preferred player when both can cover. A person in both bands counts as preferred.
- **Any band on the gig** may use either roster.
- A new override outside that entry's pool is rejected.
- Coverage risk and dietary restrictions use the current band's active roster. Coverage risk also uses that band's active in-rotation songs and gigs that include the band.

## Out of scope

- Band-scoped roles and membership-based viewing restrictions
- Treating `maybe_available` as partial attendance in the solver
- Redesigning the part-assignment page beyond filtering it to the current band's active songs
- Redirects from the old unscoped URLs
