# Bulk Edit Part Assignments

## Goal

From a song, edit who plays each part on which instrument in one save. New assignments are Ready to Perform and cannot solo. Existing readiness and solo flags stay unless that person is removed.

## Context

`SongPart` names the parts on a song. `PartAssignment` is one member on one part on one instrument, with `performance_readiness` and `can_solo`. Uniqueness is `(member, song_part, instrument)`.

Song detail lists assignments for active members of the current band and links to a one-at-a-time create form. Songs can be linked to more than one band. Assignments are not band-scoped, so this page must only add and remove people who are active in the band being edited.

No model change.

## Success criteria

- Song detail links to “Bulk Edit Part Assignments” beside “New Part Assignment”.
- The page has one section per song part, in part order, including parts with no assignments.
- Each existing instrument for this band is one row. The instrument is fixed. Members are chips.
- A new row picks an instrument that does not already have a row on that part, then the same chip picker.
- Save creates, keeps, or deletes assignments as specified below, in one transaction.
- People who are not active members of this band never appear, and their assignments are not changed.

## Page

URL: `bands/<slug>/songs/<song_id>/part-assignments/bulk-edit/`

URL name: `song_part_assignment_bulk_edit`

Nav: Songs > song title > Bulk Edit Part Assignments.

One Save at the bottom. Cancel returns to the song. Same access as the existing part-assignment create view.

### Members

Active `BandMembership` for this band, and `user.is_active`. Same set song detail already uses. Ordered by first name, then last name.

### Existing row

One row per instrument that has at least one assignment on that part for a member in the set above. Ordered by instrument order.

- Instrument name is text, not a dropdown.
- Members who already have that assignment are chips, in name order.
- No readiness or solo text.

An instrument used only by people outside this band does not get a row. It can be chosen on a new row.

### New row

“Add row” under a part inserts:

- An instrument dropdown. Options are every instrument except those that already have a row on that part, including other new rows. Ordered by instrument order.
- The same chip picker, empty.
- “Remove row”, which discards that unsaved row only.

There is no remove control on an existing row. Clearing its chips is how those assignments are removed. After a successful save, an instrument with nobody left in this band does not come back as a row.

### Chip picker

- A chip has the member’s name and an ×. Click × to remove that chip.
- A text field follows the chips. Click or focus opens the members who are not already on this row. Typing filters by case-insensitive substring of the full name. Click a name to append a chip.
- Backspace while the field is empty removes the last chip. Backspace while it has text edits the text.
- A member already on the row is not in the list, so the same person cannot be added twice on one row.
- The same person may appear on two instruments of the same part. The model allows that.
- Clicking outside the list closes it.

The picker needs JavaScript. The posted form still carries the part id, instrument id, and member ids so the server can validate them.

Each existing row posts `part`, `instrument`, and `members` (repeat the member id once per chip). Each new row posts the same three fields, with `instrument` empty when the dropdown is unset. Member ids come from hidden inputs the picker updates. The server does not trust the browser for who is allowed.

## Save

One POST. All writes run in a single transaction. On success, redirect to the song detail page.

Posted chips are the full member list for that row, for this band only. Diff against the database at save time.

### Existing row

Keyed by the part and instrument rendered for that row.

- Still in the chips: leave the assignment alone, including readiness and `can_solo`.
- In the chips, not assigned: create `PartAssignment` with `performance_readiness=ready` (“Ready to Perform”) and `can_solo=False`.
- Assigned, chip removed: delete that assignment.
- No chips: delete this band’s assignments for that part and instrument.
- Assignments whose member is outside the active set are not loaded and not deleted.

Before any delete, an existing key is valid only when this band still has an active-member assignment for that part and instrument. Otherwise the save is rejected.

### New row

- Each chip creates an assignment, Ready to Perform, `can_solo=False`.
- No chips: ignore the row.
- Instrument already used by another row for that part in this POST (existing or new): reject the whole save. Clearing the chips on an existing row does not free that instrument for a new row in the same save.
- Member already assigned to that part and instrument: skip. Do not create a second row.
- The same member id posted twice on one row: store one assignment.

Creating on an instrument that has assignments only for people outside this band is allowed. Those other assignments stay.

## Errors

A rejected save writes nothing. The page redisplays with the same rows, instruments, and chips, and the error at the top.

Reject when:

- A new row has members and no instrument.
- Two rows on the same part use the same instrument.
- A posted member is not an active member of this band.
- A posted part is not on this song, or the instrument does not exist.
- A posted existing row does not match a current assignment for this band on this song.

Ignore, without an error:

- A new row with no members.
- A duplicate member id on one row (store once).
- A create that matches an assignment that already exists.

Deletes only hit an assignment for this song, the posted part, the posted instrument, and an active member of this band whose chip was removed.

## Testing

Django tests, same style as the existing part-assignment tests.

1. A new chip creates an assignment: Ready to Perform, `can_solo=False`.
2. A chip left in place keeps its readiness and `can_solo`.
3. A removed chip deletes that assignment.
4. Clearing every chip deletes this band’s assignments for that part and instrument.
5. Another band’s member on the same part and instrument is not deleted.
6. An inactive member’s assignment is not deleted.
7. Two rows, same part, same instrument: save rejected, nothing written.
8. A new row with members and no instrument: save rejected, nothing written.
9. A member not in this band, or a part from another song: save rejected.
10. The same member posted twice on one row becomes one assignment.
11. A new row with no members is ignored.
12. A part with nobody assigned still renders a section.
13. Song detail shows the Bulk Edit Part Assignments link.

## Out of scope

- Editing readiness or solo on this page.
- Changing the instrument on an existing row.
- Deleting or editing assignments for people outside the current band.
- A new model, admin change, or third-party picker library.
