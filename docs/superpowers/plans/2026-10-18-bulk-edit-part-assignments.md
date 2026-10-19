# Bulk Edit Part Assignments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a band replace the member list for each song part and instrument in one save, creating new assignments as Ready to Perform with no solo.

**Architecture:** Pure parse/apply/page functions live in `band/part_assignment_bulk.py`. A thin Django view posts indexed `part` / `instrument` / `members` fields and rerenders on error. The template server-renders hidden fields so a save without JavaScript keeps the current rows; the chip picker rewrites those fields on submit.

**Tech Stack:** Django 5, Django TestCase, Django templates, plain JavaScript, existing `band/css/style.css`.

## Global Constraints

- No model change, no migration, no admin change, no third-party picker library.
- New assignments use `performance_readiness=ready` (“Ready to Perform”) and `can_solo=False`.
- A chip left in place keeps that assignment’s readiness and `can_solo`.
- Only active members of the band being edited (`BandMembership.is_active` and `user.is_active`) are shown, added, or deleted.
- Existing-row instrument is fixed. A new row’s dropdown hides instruments that already have a row on that part.
- Clearing chips on an existing row does not free that instrument for a new row in the same save.
- One POST, one transaction. A rejected save writes nothing and redisplays the same rows, instruments, and chips with the error at the top.
- Success redirects to song detail.
- Same access as the existing part-assignment create view.

## File Structure

| File | Responsibility |
|------|----------------|
| `prsb/band/part_assignment_bulk.py` | Active-member query, POST parse, validate-and-apply, page context |
| `prsb/band/views.py` | `SongPartAssignmentBulkEditView` only |
| `prsb/band/urls.py` | `song_part_assignment_bulk_edit` |
| `prsb/band/templates/band/partassignment_bulk_edit.html` | Sections, server-rendered fields, picker hooks |
| `prsb/band/templates/band/song_detail.html` | Link next to New Part Assignment |
| `prsb/band/static/band/js/part_assignment_bulk_edit.js` | Add row, instrument exclusion, chip picker, rewrite hidden fields |
| `prsb/band/static/band/css/style.css` | Chip and picker layout |
| `prsb/band/tests.py` | Apply tests and view tests |

---

### Task 1: Parse and apply bulk edits

**Files:**
- Create: `prsb/band/part_assignment_bulk.py`
- Modify: `prsb/band/tests.py`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `Song`, `SongPart`, `Instrument`, `Band`, `BandMember`, `BandMembership`, `PartAssignment`, `PerformanceReadiness`
- Produces:
  - `PostedRow(kind: str, part_id: int, instrument_id: int | None, member_ids: tuple[int, ...])`
  - `parse_posted_rows(post) -> list[PostedRow]`
  - `apply_bulk_part_assignments(song, band, rows: list[PostedRow]) -> list[str]`
  - `active_members(band)`
  - `bulk_edit_page(song, band, rows: list[PostedRow] | None = None) -> dict`
  - Error strings, exact:
    - `"Choose an instrument for each new row that has members."`
    - `"Each instrument can only appear once on a part."`
    - `"A selected member is not in this band."`
    - `"A part on this form is not on this song."`
    - `"Choose a real instrument."`
    - `"That part and instrument is not an existing row for this band."`

POST shape: `existing-TOTAL`, `existing-<i>-part`, `existing-<i>-instrument`, repeated `existing-<i>-members`; same with the `new-` prefix. Empty `instrument` means unset.

- [ ] **Step 1: Write the failing tests**

Add this import to the import block in `prsb/band/tests.py`:

```python
from django.http import QueryDict
```

Append this class at the end of `prsb/band/tests.py`:

```python
class BulkEditPartAssignmentTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.band = Band.objects.get(slug="prsb")
        cls.other_band = Band.objects.create(name="Other Bulk Band", slug="other-bulk-band")
        cls.song = Song.objects.create(title="Bulk Edit Song")
        BandSong.objects.create(band=cls.band, song=cls.song, in_gig_rotation=True)
        cls.lead = SongPart.objects.create(song=cls.song, name="Bulk Lead", has_solo=True)
        cls.pad = SongPart.objects.create(song=cls.song, name="Bulk Pad", has_solo=False)
        cls.other_song = Song.objects.create(title="Bulk Other Song")
        cls.foreign_part = SongPart.objects.create(song=cls.other_song, name="Foreign Part")
        cls.trumpet = Instrument.objects.create(name="Bulk Trumpet", order=10)
        cls.trombone = Instrument.objects.create(name="Bulk Trombone", order=11)
        cls.ada = User.objects.create_user(username="bulk_ada", first_name="Ada", last_name="Alpha")
        cls.bea = User.objects.create_user(username="bulk_bea", first_name="Bea", last_name="Beta")
        cls.cy = User.objects.create_user(username="bulk_cy", first_name="Cy", last_name="Inactive")
        cls.dee = User.objects.create_user(username="bulk_dee", first_name="Dee", last_name="Other")
        BandMembership.objects.create(band=cls.band, member=cls.ada.bandmember, is_active=True)
        BandMembership.objects.create(band=cls.band, member=cls.bea.bandmember, is_active=True)
        BandMembership.objects.create(band=cls.band, member=cls.cy.bandmember, is_active=False)
        BandMembership.objects.create(band=cls.other_band, member=cls.dee.bandmember, is_active=True)

    def _assign(self, user, part, instrument, readiness=PerformanceReadiness.READY, can_solo=False):
        return PartAssignment.objects.create(
            member=user.bandmember,
            song_part=part,
            instrument=instrument,
            performance_readiness=readiness,
            can_solo=can_solo,
        )

    def _post(self, **fields):
        data = QueryDict(mutable=True)
        for key, value in fields.items():
            if isinstance(value, list):
                data.setlist(key, [str(item) for item in value])
            else:
                data[key] = "" if value is None else str(value)
        return data

    def _apply(self, data):
        from band.part_assignment_bulk import apply_bulk_part_assignments, parse_posted_rows
        return apply_bulk_part_assignments(self.song, self.band, parse_posted_rows(data))

    def _snapshot(self):
        return set(
            PartAssignment.objects.filter(song_part__song=self.song).values_list(
                "member_id", "song_part_id", "instrument_id", "performance_readiness", "can_solo",
            )
        )

    def test_new_chip_creates_ready_no_solo(self):
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        assignment = PartAssignment.objects.get(
            member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet,
        )
        self.assertEqual(assignment.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(assignment.can_solo)

    def test_member_added_to_existing_row_is_ready_and_not_solo(self):
        self._assign(self.ada, self.lead, self.trumpet, PerformanceReadiness.BACKUP, can_solo=True)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [self.ada.bandmember.pk, self.bea.bandmember.pk],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        ada = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(ada.performance_readiness, PerformanceReadiness.BACKUP)
        self.assertTrue(ada.can_solo)
        bea = PartAssignment.objects.get(member=self.bea.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(bea.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(bea.can_solo)

    def test_kept_chip_preserves_readiness_and_solo(self):
        self._assign(self.ada, self.lead, self.trumpet, PerformanceReadiness.BACKUP, can_solo=True)
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [self.ada.bandmember.pk],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertEqual(self._snapshot(), before)

    def test_removed_chip_deletes_assignment(self):
        self._assign(self.ada, self.lead, self.trumpet, PerformanceReadiness.BACKUP, can_solo=True)
        self._assign(self.bea, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [self.ada.bandmember.pk],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertFalse(PartAssignment.objects.filter(member=self.bea.bandmember, song_part=self.lead).exists())
        kept = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead)
        self.assertEqual(kept.performance_readiness, PerformanceReadiness.BACKUP)
        self.assertTrue(kept.can_solo)

    def test_clearing_chips_deletes_this_bands_assignments(self):
        self._assign(self.ada, self.lead, self.trumpet)
        self._assign(self.bea, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertFalse(
            PartAssignment.objects.filter(song_part=self.lead, instrument=self.trumpet, member__in=[self.ada.bandmember, self.bea.bandmember]).exists()
        )

    def test_other_band_member_is_not_deleted(self):
        self._assign(self.ada, self.lead, self.trumpet)
        self._assign(self.dee, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertFalse(PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead).exists())
        self.assertTrue(PartAssignment.objects.filter(member=self.dee.bandmember, song_part=self.lead, instrument=self.trumpet).exists())

    def test_inactive_member_assignment_is_not_deleted(self):
        self._assign(self.ada, self.lead, self.trumpet)
        self._assign(self.cy, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertTrue(PartAssignment.objects.filter(member=self.cy.bandmember, song_part=self.lead, instrument=self.trumpet).exists())
        self.assertFalse(PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead).exists())

    def test_duplicate_instrument_on_a_part_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 2,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
            "new-1-part": self.lead.pk,
            "new-1-instrument": self.trumpet.pk,
            "new-1-members": [self.bea.bandmember.pk],
        }))
        self.assertIn("Each instrument can only appear once on a part.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_new_row_cannot_reuse_an_existing_instrument(self):
        self._assign(self.ada, self.lead, self.trumpet)
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [],
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.bea.bandmember.pk],
        }))
        self.assertIn("Each instrument can only appear once on a part.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_new_row_with_members_and_no_instrument_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": "",
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertIn("Choose an instrument for each new row that has members.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_member_not_in_band_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.dee.bandmember.pk],
        }))
        self.assertIn("A selected member is not in this band.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_part_from_another_song_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.foreign_part.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertIn("A part on this form is not on this song.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_unknown_instrument_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": 999999,
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertIn("Choose a real instrument.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_unknown_existing_row_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trombone.pk,
            "existing-0-members": [self.ada.bandmember.pk],
            "new-TOTAL": 0,
        }))
        self.assertIn("That part and instrument is not an existing row for this band.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_duplicate_member_on_one_row_creates_one_assignment(self):
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk, self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        self.assertEqual(
            PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet).count(),
            1,
        )

    def test_empty_new_row_is_ignored(self):
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 2,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [],
            "new-1-part": self.lead.pk,
            "new-1-instrument": self.trombone.pk,
            "new-1-members": [self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        self.assertFalse(PartAssignment.objects.filter(song_part=self.lead, instrument=self.trumpet).exists())
        assignment = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead, instrument=self.trombone)
        self.assertEqual(assignment.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(assignment.can_solo)

    def test_new_row_skips_member_already_assigned(self):
        self._assign(self.ada, self.lead, self.trumpet, PerformanceReadiness.BACKUP, can_solo=True)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk, self.bea.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        ada = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(ada.performance_readiness, PerformanceReadiness.BACKUP)
        self.assertTrue(ada.can_solo)
        bea = PartAssignment.objects.get(member=self.bea.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(bea.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(bea.can_solo)

    def test_same_member_can_be_on_two_instruments(self):
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 2,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
            "new-1-part": self.lead.pk,
            "new-1-instrument": self.trombone.pk,
            "new-1-members": [self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        self.assertEqual(PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead).count(), 2)

    def test_new_row_can_use_instrument_held_only_outside_this_band(self):
        self._assign(self.dee, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        self.assertTrue(PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet).exists())
        self.assertTrue(PartAssignment.objects.filter(member=self.dee.bandmember, song_part=self.lead, instrument=self.trumpet).exists())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd prsb && poetry run python manage.py test band.tests.BulkEditPartAssignmentTestCase -v 2`

Expected: FAIL with `ModuleNotFoundError: No module named 'band.part_assignment_bulk'`

- [ ] **Step 3: Write the apply module**

Create `prsb/band/part_assignment_bulk.py`:

```python
from dataclasses import dataclass

from django.db import transaction

from .models import BandMember, Instrument, PartAssignment, PerformanceReadiness, SongPart

ERR_INSTRUMENT_REQUIRED = "Choose an instrument for each new row that has members."
ERR_DUPLICATE_INSTRUMENT = "Each instrument can only appear once on a part."
ERR_MEMBER = "A selected member is not in this band."
ERR_PART = "A part on this form is not on this song."
ERR_INSTRUMENT = "Choose a real instrument."
ERR_EXISTING = "That part and instrument is not an existing row for this band."


@dataclass(frozen=True)
class PostedRow:
    kind: str
    part_id: int
    instrument_id: int | None
    member_ids: tuple[int, ...]


def active_members(band):
    return BandMember.objects.filter(
        bandmembership__band=band,
        bandmembership__is_active=True,
        user__is_active=True,
    ).select_related("user").order_by("user__first_name", "user__last_name")


def _total(post, key):
    raw = post.get(key, "0")
    if raw is None or not str(raw).isdigit():
        return 0
    return int(raw)


def _int_or_missing(raw):
    if raw is None or raw == "":
        return None
    if str(raw).isdigit():
        return int(raw)
    return -1


def parse_posted_rows(post):
    rows = []
    for kind in ("existing", "new"):
        for index in range(_total(post, f"{kind}-TOTAL")):
            prefix = f"{kind}-{index}-"
            member_ids = []
            for raw in post.getlist(prefix + "members"):
                parsed = _int_or_missing(raw)
                member_ids.append(-1 if parsed is None else parsed)
            part_id = _int_or_missing(post.get(prefix + "part", ""))
            rows.append(PostedRow(
                kind=kind,
                part_id=-1 if part_id is None else part_id,
                instrument_id=_int_or_missing(post.get(prefix + "instrument", "")),
                member_ids=tuple(member_ids),
            ))
    return rows


def _dedupe(row):
    seen = set()
    member_ids = []
    for member_id in row.member_ids:
        if member_id in seen:
            continue
        seen.add(member_id)
        member_ids.append(member_id)
    return PostedRow(row.kind, row.part_id, row.instrument_id, tuple(member_ids))


def _add_error(errors, message):
    if message not in errors:
        errors.append(message)


def apply_bulk_part_assignments(song, band, rows):
    prepared = []
    for row in rows:
        row = _dedupe(row)
        if row.kind == "new" and not row.member_ids:
            continue
        prepared.append(row)

    members = list(active_members(band))
    active_ids = {member.pk for member in members}
    part_ids = set(SongPart.objects.filter(song=song).values_list("pk", flat=True))
    instrument_ids = set(Instrument.objects.values_list("pk", flat=True))
    current_by_key = {}
    for assignment in PartAssignment.objects.filter(song_part__song=song, member_id__in=active_ids):
        current_by_key.setdefault((assignment.song_part_id, assignment.instrument_id), {})[assignment.member_id] = assignment

    errors = []
    seen_keys = set()
    for row in prepared:
        if row.part_id not in part_ids:
            _add_error(errors, ERR_PART)
        if row.instrument_id is None:
            if row.kind == "new" and row.member_ids:
                _add_error(errors, ERR_INSTRUMENT_REQUIRED)
        elif row.instrument_id not in instrument_ids:
            _add_error(errors, ERR_INSTRUMENT)
        for member_id in row.member_ids:
            if member_id not in active_ids:
                _add_error(errors, ERR_MEMBER)
        if row.instrument_id is not None and row.instrument_id in instrument_ids and row.part_id in part_ids:
            key = (row.part_id, row.instrument_id)
            if key in seen_keys:
                _add_error(errors, ERR_DUPLICATE_INSTRUMENT)
            seen_keys.add(key)
        if row.kind == "existing" and (row.part_id, row.instrument_id) not in current_by_key:
            _add_error(errors, ERR_EXISTING)

    if errors:
        return errors

    with transaction.atomic():
        for row in prepared:
            if row.kind == "existing":
                current = current_by_key[(row.part_id, row.instrument_id)]
                posted = set(row.member_ids)
                for member_id, assignment in current.items():
                    if member_id not in posted:
                        assignment.delete()
                for member_id in row.member_ids:
                    if member_id in current:
                        continue
                    PartAssignment.objects.create(
                        member_id=member_id,
                        song_part_id=row.part_id,
                        instrument_id=row.instrument_id,
                        performance_readiness=PerformanceReadiness.READY,
                        can_solo=False,
                    )
            else:
                for member_id in row.member_ids:
                    if PartAssignment.objects.filter(
                        member_id=member_id,
                        song_part_id=row.part_id,
                        instrument_id=row.instrument_id,
                    ).exists():
                        continue
                    PartAssignment.objects.create(
                        member_id=member_id,
                        song_part_id=row.part_id,
                        instrument_id=row.instrument_id,
                        performance_readiness=PerformanceReadiness.READY,
                        can_solo=False,
                    )
    return []


def _chips(member_ids, names):
    return [{"id": member_id, "name": names.get(member_id, str(member_id))} for member_id in member_ids]


def _row_payload(index, instrument_id, instrument_name, member_ids, names):
    chips = _chips(member_ids, names)
    return {
        "index": index,
        "instrument_id": instrument_id,
        "instrument_name": instrument_name,
        "chips": chips,
        "selected_ids": ",".join(str(chip["id"]) for chip in chips),
    }


def bulk_edit_page(song, band, rows=None):
    members = list(active_members(band))
    names = {member.pk: member.user.get_full_name() for member in members}
    instruments = list(Instrument.objects.all())
    instrument_by_id = {instrument.pk: instrument for instrument in instruments}
    parts = list(SongPart.objects.filter(song=song).order_by("_order"))

    db_members = {}
    for assignment in PartAssignment.objects.filter(song_part__song=song, member_id__in=names.keys()):
        db_members.setdefault((assignment.song_part_id, assignment.instrument_id), set()).add(assignment.member_id)

    posted_existing = {}
    posted_new = {}
    if rows is not None:
        for row in rows:
            if row.kind == "existing":
                posted_existing[(row.part_id, row.instrument_id)] = row.member_ids
            else:
                posted_new.setdefault(row.part_id, []).append(row)

    def member_ids_for(key):
        if rows is None or key not in posted_existing:
            return [member.pk for member in members if member.pk in db_members.get(key, ())]
        return list(posted_existing[key])

    sections = []
    existing_index = 0
    new_index = 0
    for part in parts:
        keys = [key for key in db_members if key[0] == part.pk]
        keys.sort(key=lambda key: instrument_by_id[key[1]].order if key[1] in instrument_by_id else key[1])
        seen = set(keys)
        for key in posted_existing:
            if key[0] == part.pk and key not in seen:
                keys.append(key)
                seen.add(key)
        existing_rows = []
        for key in keys:
            instrument = instrument_by_id.get(key[1])
            existing_rows.append(_row_payload(
                existing_index,
                key[1],
                instrument.name if instrument is not None else str(key[1]),
                member_ids_for(key),
                names,
            ))
            existing_index += 1
        new_rows = []
        for row in posted_new.get(part.pk, []):
            instrument = instrument_by_id.get(row.instrument_id)
            new_rows.append(_row_payload(
                new_index,
                row.instrument_id,
                instrument.name if instrument is not None else "",
                row.member_ids,
                names,
            ))
            new_index += 1
        sections.append({"part": part, "existing_rows": existing_rows, "new_rows": new_rows})

    return {
        "picker_members": [{"id": member.pk, "name": names[member.pk]} for member in members],
        "picker_instruments": [{"id": instrument.pk, "name": instrument.name} for instrument in instruments],
        "sections": sections,
        "existing_total": existing_index,
        "new_total": new_index,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd prsb && poetry run python manage.py test band.tests.BulkEditPartAssignmentTestCase -v 2`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prsb/band/part_assignment_bulk.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add bulk part-assignment save rules.

New chips become Ready to Perform with no solo, and people outside the active band are left alone.

EOF
)"
```

---

### Task 2: Page, URL, and song-detail link

**Files:**
- Modify: `prsb/band/views.py` (after `SongPartAssignmentUpdateView`)
- Modify: `prsb/band/urls.py` (after `song_part_assignment_update`)
- Create: `prsb/band/templates/band/partassignment_bulk_edit.html`
- Modify: `prsb/band/templates/band/song_detail.html` (the New Part Assignment link)
- Modify: `prsb/band/tests.py`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `parse_posted_rows`, `apply_bulk_part_assignments`, `bulk_edit_page`, `BandScopedMixin.band_context`
- Produces:
  - URL name `song_part_assignment_bulk_edit`
  - Path `bands/<slug:slug>/songs/<int:song_id>/part-assignments/bulk-edit/`
  - `SongPartAssignmentBulkEditView`
  - Template hooks task 3 binds to: `.bulk-part` (`data-part-id`), `.bulk-row` (`data-kind`, existing rows also `data-instrument-id`), `.bulk-new-rows`, `.bulk-add-row`, `.bulk-remove-row`, `.bulk-instrument-select`, `.member-picker` (`data-selected`), `[data-server-fields]`, `#bulk-members`, `#bulk-instruments`

- [ ] **Step 1: Write the failing view tests**

Append this class at the end of `prsb/band/tests.py`:

```python
class BulkEditPartAssignmentViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.band = Band.objects.get(slug="prsb")
        cls.song = Song.objects.create(title="Bulk Edit Page Song")
        BandSong.objects.create(band=cls.band, song=cls.song, in_gig_rotation=True)
        cls.lead = SongPart.objects.create(song=cls.song, name="Page Lead", has_solo=True)
        cls.pad = SongPart.objects.create(song=cls.song, name="Page Pad", has_solo=False)
        cls.trumpet = Instrument.objects.create(name="Page Trumpet", order=20)
        cls.trombone = Instrument.objects.create(name="Page Trombone", order=21)
        cls.ada = User.objects.create_user(username="bulk_page_ada", first_name="Ada", last_name="Page")
        cls.bea = User.objects.create_user(username="bulk_page_bea", first_name="Bea", last_name="Page")
        BandMembership.objects.create(band=cls.band, member=cls.ada.bandmember, is_active=True)
        BandMembership.objects.create(band=cls.band, member=cls.bea.bandmember, is_active=True)
        PartAssignment.objects.create(
            member=cls.ada.bandmember,
            song_part=cls.lead,
            instrument=cls.trumpet,
            performance_readiness=PerformanceReadiness.BACKUP,
            can_solo=True,
        )

    def setUp(self):
        self.url = reverse(
            "band:song_part_assignment_bulk_edit",
            kwargs={"slug": "prsb", "song_id": self.song.pk},
        )

    def test_song_detail_links_to_bulk_edit(self):
        detail = self.client.get(reverse("band:song_detail", kwargs={"slug": "prsb", "pk": self.song.pk}))
        self.assertContains(detail, "Bulk Edit Part Assignments")
        self.assertContains(detail, self.url)

    def test_get_renders_every_part_and_existing_assignment(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertLess(body.index("Page Lead"), body.index("Page Pad"))
        self.assertContains(response, "Page Trumpet")
        self.assertContains(response, "Ada Page")
        self.assertContains(response, "Add row")
        self.assertNotContains(response, "Backup")
        self.assertNotContains(response, "solo")
        self.assertContains(response, 'name="existing-TOTAL"')
        self.assertContains(response, f'name="existing-0-members" value="{self.ada.bandmember.pk}"')

    def test_post_creates_and_redirects(self):
        response = self.client.post(self.url, {
            "existing-TOTAL": "1",
            "existing-0-part": str(self.lead.pk),
            "existing-0-instrument": str(self.trumpet.pk),
            "existing-0-members": str(self.ada.bandmember.pk),
            "new-TOTAL": "1",
            "new-0-part": str(self.pad.pk),
            "new-0-instrument": str(self.trombone.pk),
            "new-0-members": str(self.bea.bandmember.pk),
        })
        self.assertRedirects(response, reverse("band:song_detail", kwargs={"slug": "prsb", "pk": self.song.pk}))
        created = PartAssignment.objects.get(member=self.bea.bandmember, song_part=self.pad, instrument=self.trombone)
        self.assertEqual(created.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(created.can_solo)
        kept = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(kept.performance_readiness, PerformanceReadiness.BACKUP)
        self.assertTrue(kept.can_solo)

    def test_rejected_post_redisplays_chips_and_writes_nothing(self):
        before = set(PartAssignment.objects.values_list(
            "member_id", "song_part_id", "instrument_id", "performance_readiness", "can_solo",
        ))
        response = self.client.post(self.url, {
            "existing-TOTAL": "1",
            "existing-0-part": str(self.lead.pk),
            "existing-0-instrument": str(self.trumpet.pk),
            "existing-0-members": str(self.ada.bandmember.pk),
            "new-TOTAL": "2",
            "new-0-part": str(self.pad.pk),
            "new-0-instrument": str(self.trombone.pk),
            "new-0-members": str(self.ada.bandmember.pk),
            "new-1-part": str(self.pad.pk),
            "new-1-instrument": str(self.trombone.pk),
            "new-1-members": str(self.bea.bandmember.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Each instrument can only appear once on a part.")
        self.assertContains(response, "Ada Page")
        self.assertContains(response, "Bea Page")
        self.assertEqual(before, set(PartAssignment.objects.values_list(
            "member_id", "song_part_id", "instrument_id", "performance_readiness", "can_solo",
        )))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd prsb && poetry run python manage.py test band.tests.BulkEditPartAssignmentViewTestCase -v 2`

Expected: FAIL with `NoReverseMatch` for `song_part_assignment_bulk_edit`

- [ ] **Step 3: Add the URL, view, template, and link**

In `prsb/band/urls.py`, immediately after the `song_part_assignment_update` path, add:

```python
    path(
        "bands/<slug:slug>/songs/<int:song_id>/part-assignments/bulk-edit/",
        views.SongPartAssignmentBulkEditView.as_view(),
        name="song_part_assignment_bulk_edit",
    ),
```

In `prsb/band/views.py`, add this import with the other local imports:

```python
from .part_assignment_bulk import apply_bulk_part_assignments, bulk_edit_page, parse_posted_rows
```

Immediately after `SongPartAssignmentUpdateView`, add:

```python
class SongPartAssignmentBulkEditView(BandScopedMixin, generic.View):
    template_name = "band/partassignment_bulk_edit.html"

    def _song(self):
        return get_object_or_404(Song, pk=self.kwargs["song_id"])

    def _context(self, song, errors, rows):
        return self.band_context(
            song=song,
            errors=errors or [],
            **bulk_edit_page(song, self.band, rows),
        )

    def get(self, request, slug, song_id):
        song = self._song()
        return render(request, self.template_name, self._context(song, errors=None, rows=None))

    def post(self, request, slug, song_id):
        song = self._song()
        rows = parse_posted_rows(request.POST)
        errors = apply_bulk_part_assignments(song, self.band, rows)
        if errors:
            return render(request, self.template_name, self._context(song, errors=errors, rows=rows))
        return redirect("band:song_detail", slug=self.band.slug, pk=song.pk)
```

Create `prsb/band/templates/band/partassignment_bulk_edit.html`:

```html
{% extends "band/base.html" %}
{% load static %}

{% block title %}Bulk Edit Part Assignments | PRSB{% endblock %}

{% block nav %}
    > <a href={% url 'band:song_list' band.slug %}>Songs</a>
    > <a href={% url 'band:song_detail' band.slug song.pk %}>{{ song.title }}</a>
    > Bulk Edit Part Assignments
{% endblock %}

{% block content %}
    <h1>Bulk Edit Part Assignments</h1>
    <p>{{ song.title }}</p>

    {% if errors %}
        <ul class="errorlist">
            {% for error in errors %}
                <li>{{ error }}</li>
            {% endfor %}
        </ul>
    {% endif %}

    <form method="post" id="bulk-edit-form">{% csrf_token %}
        <input type="hidden" data-server-fields name="existing-TOTAL" value="{{ existing_total }}">
        <input type="hidden" data-server-fields name="new-TOTAL" value="{{ new_total }}">

        {% for section in sections %}
            <section class="bulk-part" data-part-id="{{ section.part.pk }}">
                <h2>{{ section.part.name }}</h2>
                {% for row in section.existing_rows %}
                    <div class="bulk-row" data-kind="existing" data-instrument-id="{{ row.instrument_id }}">
                        <span class="bulk-instrument">{{ row.instrument_name }}</span>
                        <div class="member-picker" data-selected="{{ row.selected_ids }}">
                            {% for chip in row.chips %}
                                <span class="member-chip">{{ chip.name }}</span>
                            {% endfor %}
                        </div>
                        <input type="hidden" data-server-fields name="existing-{{ row.index }}-part" value="{{ section.part.pk }}">
                        <input type="hidden" data-server-fields name="existing-{{ row.index }}-instrument" value="{{ row.instrument_id }}">
                        {% for chip in row.chips %}
                            <input type="hidden" data-server-fields name="existing-{{ row.index }}-members" value="{{ chip.id }}">
                        {% endfor %}
                    </div>
                {% endfor %}
                <div class="bulk-new-rows">
                    {% for row in section.new_rows %}
                        <div class="bulk-row" data-kind="new">
                            <select class="bulk-instrument-select">
                                <option value="">---------</option>
                                {% for instrument in picker_instruments %}
                                    <option value="{{ instrument.id }}" {% if instrument.id == row.instrument_id %}selected{% endif %}>{{ instrument.name }}</option>
                                {% endfor %}
                            </select>
                            <div class="member-picker" data-selected="{{ row.selected_ids }}">
                                {% for chip in row.chips %}
                                    <span class="member-chip">{{ chip.name }}</span>
                                {% endfor %}
                            </div>
                            <button type="button" class="bulk-remove-row">Remove row</button>
                            <input type="hidden" data-server-fields name="new-{{ row.index }}-part" value="{{ section.part.pk }}">
                            <input type="hidden" data-server-fields name="new-{{ row.index }}-instrument" value="{{ row.instrument_id|default_if_none:'' }}">
                            {% for chip in row.chips %}
                                <input type="hidden" data-server-fields name="new-{{ row.index }}-members" value="{{ chip.id }}">
                            {% endfor %}
                        </div>
                    {% endfor %}
                </div>
                <button type="button" class="bulk-add-row">Add row</button>
            </section>
        {% endfor %}

        <input type="submit" value="Save">
        <a href={% url 'band:song_detail' band.slug song.pk %}>Cancel</a>
    </form>
    {{ picker_members|json_script:"bulk-members" }}
    {{ picker_instruments|json_script:"bulk-instruments" }}
{% endblock %}
```

In `prsb/band/templates/band/song_detail.html`, put the bulk-edit link on the next line after the New Part Assignment link:

```html
        <a href={% url 'band:song_part_assignment_create' band.slug song.pk %}>New Part Assignment</a>
        <a href={% url 'band:song_part_assignment_bulk_edit' band.slug song.pk %}>Bulk Edit Part Assignments</a>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd prsb && poetry run python manage.py test band.tests.BulkEditPartAssignmentTestCase band.tests.BulkEditPartAssignmentViewTestCase -v 2`

Expected: PASS

The view test asserts the page does not contain the substrings `Backup` or `solo`. Do not add those words to this template.

- [ ] **Step 5: Commit**

```bash
git add prsb/band/views.py prsb/band/urls.py prsb/band/templates/band/partassignment_bulk_edit.html prsb/band/templates/band/song_detail.html prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add the bulk part-assignment page.

Song detail links to one form per song, and a rejected save redisplays the posted rows.

EOF
)"
```

---

### Task 3: Chip picker

**Files:**
- Create: `prsb/band/static/band/js/part_assignment_bulk_edit.js`
- Modify: `prsb/band/templates/band/partassignment_bulk_edit.html`
- Modify: `prsb/band/static/band/css/style.css`
- Modify: `prsb/band/tests.py`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: the template hooks from Task 2, plus `#bulk-members` and `#bulk-instruments` JSON (`[{id, name}, ...]`)
- Produces: on submit, the form’s `[data-server-fields]` inputs are removed and replaced from the live rows. Existing rows post `existing-<i>-part`, `existing-<i>-instrument`, and one `existing-<i>-members` per chip. New rows post the `new-` versions. `existing-TOTAL` and `new-TOTAL` match the row counts.

- [ ] **Step 1: Write the failing test**

Add this method to `BulkEditPartAssignmentViewTestCase`:

```python
    def test_page_includes_picker_script(self):
        response = self.client.get(self.url)
        self.assertContains(response, "band/js/part_assignment_bulk_edit.js")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd prsb && poetry run python manage.py test band.tests.BulkEditPartAssignmentViewTestCase.test_page_includes_picker_script -v 2`

Expected: FAIL because the response does not contain `band/js/part_assignment_bulk_edit.js`

- [ ] **Step 3: Add the script, stylesheet, and script tag**

Append to `prsb/band/static/band/css/style.css`:

```css
.bulk-part {
    margin-bottom: 1.5rem;
}

.bulk-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: flex-start;
    margin: 8px 0;
}

.bulk-instrument {
    min-width: 8rem;
    padding-top: 6px;
}

.member-picker {
    position: relative;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    align-items: center;
    min-width: 16rem;
    border: 1px solid #0D283C;
    padding: 4px;
}

.member-chip {
    background: #FFB627;
    color: #0D283C;
    border-radius: 4px;
    padding: 2px 6px;
}

.member-picker input,
.member-chip button,
.member-picker-menu button {
    margin-bottom: 0;
    padding: 2px 6px;
    background: transparent;
    border: 0;
    color: inherit;
}

.member-picker-menu {
    position: absolute;
    z-index: 2;
    left: 0;
    top: 100%;
    background: #fff;
    border: 1px solid #0D283C;
    margin: 0;
    padding: 0;
    list-style: none;
    max-height: 12rem;
    overflow: auto;
    min-width: 12rem;
}

.member-picker-menu button {
    display: block;
    width: 100%;
    text-align: left;
    cursor: pointer;
    color: #0D283C;
}
```

Create `prsb/band/static/band/js/part_assignment_bulk_edit.js`:

```javascript
(function () {
    const form = document.getElementById("bulk-edit-form");
    if (!form) {
        return;
    }

    const members = JSON.parse(document.getElementById("bulk-members").textContent);
    const instruments = JSON.parse(document.getElementById("bulk-instruments").textContent);

    function catalogName(id) {
        const member = members.find(function (item) { return String(item.id) === String(id); });
        return member ? member.name : String(id);
    }

    function initPicker(picker) {
        const selected = (picker.dataset.selected || "").split(",").filter(Boolean);
        picker.textContent = "";
        const chips = document.createElement("div");
        chips.className = "member-chips";
        const input = document.createElement("input");
        input.type = "text";
        input.autocomplete = "off";
        input.setAttribute("aria-label", "Add member");
        const menu = document.createElement("ul");
        menu.className = "member-picker-menu";
        menu.hidden = true;
        picker.append(chips, input, menu);

        function renderChips() {
            chips.textContent = "";
            selected.forEach(function (id) {
                const chip = document.createElement("span");
                chip.className = "member-chip";
                chip.dataset.memberId = String(id);
                chip.append(document.createTextNode(catalogName(id) + " "));
                const remove = document.createElement("button");
                remove.type = "button";
                remove.textContent = "×";
                remove.setAttribute("aria-label", "Remove " + catalogName(id));
                remove.addEventListener("click", function () {
                    const index = selected.indexOf(String(id));
                    if (index !== -1) {
                        selected.splice(index, 1);
                    }
                    renderChips();
                    renderMenu();
                });
                chip.append(remove);
                chips.append(chip);
            });
        }

        function renderMenu() {
            menu.textContent = "";
            const query = input.value.trim().toLowerCase();
            members.forEach(function (member) {
                const id = String(member.id);
                if (selected.indexOf(id) !== -1) {
                    return;
                }
                if (query && member.name.toLowerCase().indexOf(query) === -1) {
                    return;
                }
                const item = document.createElement("li");
                const button = document.createElement("button");
                button.type = "button";
                button.textContent = member.name;
                button.addEventListener("mousedown", function (event) {
                    event.preventDefault();
                });
                button.addEventListener("click", function () {
                    selected.push(id);
                    input.value = "";
                    renderChips();
                    renderMenu();
                    input.focus();
                });
                item.append(button);
                menu.append(item);
            });
        }

        input.addEventListener("focus", function () {
            renderMenu();
            menu.hidden = false;
        });
        input.addEventListener("input", function () {
            renderMenu();
            menu.hidden = false;
        });
        input.addEventListener("keydown", function (event) {
            if (event.key === "Backspace" && input.value === "" && selected.length) {
                event.preventDefault();
                selected.pop();
                renderChips();
                renderMenu();
            }
        });
        document.addEventListener("click", function (event) {
            if (!picker.contains(event.target)) {
                menu.hidden = true;
            }
        });
        renderChips();
    }

    function usedInstrumentIds(part, exceptSelect) {
        const used = new Set();
        part.querySelectorAll(".bulk-row[data-kind='existing']").forEach(function (row) {
            if (row.dataset.instrumentId) {
                used.add(String(row.dataset.instrumentId));
            }
        });
        part.querySelectorAll(".bulk-row[data-kind='new'] select").forEach(function (select) {
            if (select !== exceptSelect && select.value) {
                used.add(select.value);
            }
        });
        return used;
    }

    function fillInstrumentSelect(select, part) {
        const current = select.value;
        const used = usedInstrumentIds(part, select);
        select.textContent = "";
        const blank = document.createElement("option");
        blank.value = "";
        blank.textContent = "---------";
        select.append(blank);
        instruments.forEach(function (instrument) {
            const id = String(instrument.id);
            if (used.has(id) && id !== current) {
                return;
            }
            const option = document.createElement("option");
            option.value = id;
            option.textContent = instrument.name;
            if (id === current) {
                option.selected = true;
            }
            select.append(option);
        });
    }

    function refreshInstrumentSelects(part) {
        part.querySelectorAll(".bulk-row[data-kind='new'] select").forEach(function (select) {
            fillInstrumentSelect(select, part);
        });
    }

    function bindNewRow(row, part) {
        const select = row.querySelector("select");
        select.addEventListener("change", function () {
            refreshInstrumentSelects(part);
        });
        row.querySelector(".bulk-remove-row").addEventListener("click", function () {
            row.remove();
            refreshInstrumentSelects(part);
        });
        fillInstrumentSelect(select, part);
        initPicker(row.querySelector(".member-picker"));
    }

    function addRow(part) {
        const row = document.createElement("div");
        row.className = "bulk-row";
        row.dataset.kind = "new";
        const select = document.createElement("select");
        select.className = "bulk-instrument-select";
        const picker = document.createElement("div");
        picker.className = "member-picker";
        picker.dataset.selected = "";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "bulk-remove-row";
        remove.textContent = "Remove row";
        row.append(select, picker, remove);
        part.querySelector(".bulk-new-rows").append(row);
        bindNewRow(row, part);
    }

    function appendField(name, value) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        input.dataset.generatedField = "1";
        form.append(input);
    }

    form.addEventListener("submit", function () {
        form.querySelectorAll("[data-server-fields]").forEach(function (field) {
            field.remove();
        });
        form.querySelectorAll("[data-generated-field]").forEach(function (field) {
            field.remove();
        });
        let existingIndex = 0;
        let newIndex = 0;
        form.querySelectorAll(".bulk-part").forEach(function (part) {
            const partId = part.dataset.partId;
            part.querySelectorAll(".bulk-row[data-kind='existing']").forEach(function (row) {
                appendField("existing-" + existingIndex + "-part", partId);
                appendField("existing-" + existingIndex + "-instrument", row.dataset.instrumentId);
                row.querySelectorAll(".member-chip").forEach(function (chip) {
                    appendField("existing-" + existingIndex + "-members", chip.dataset.memberId);
                });
                existingIndex += 1;
            });
            part.querySelectorAll(".bulk-row[data-kind='new']").forEach(function (row) {
                appendField("new-" + newIndex + "-part", partId);
                appendField("new-" + newIndex + "-instrument", row.querySelector("select").value);
                row.querySelectorAll(".member-chip").forEach(function (chip) {
                    appendField("new-" + newIndex + "-members", chip.dataset.memberId);
                });
                newIndex += 1;
            });
        });
        appendField("existing-TOTAL", String(existingIndex));
        appendField("new-TOTAL", String(newIndex));
    });

    form.querySelectorAll(".bulk-part").forEach(function (part) {
        part.querySelectorAll(".bulk-row[data-kind='existing'] .member-picker").forEach(initPicker);
        part.querySelectorAll(".bulk-row[data-kind='new']").forEach(function (row) {
            bindNewRow(row, part);
        });
        part.querySelector(".bulk-add-row").addEventListener("click", function () {
            addRow(part);
        });
    });
})();
```

In `prsb/band/templates/band/partassignment_bulk_edit.html`, before `{% endblock %}` of `content`, after the two `json_script` lines, add:

```html
    <script src="{% static 'band/js/part_assignment_bulk_edit.js' %}"></script>
```

`{% load static %}` is already in that template.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd prsb && poetry run python manage.py test band.tests.BulkEditPartAssignmentTestCase band.tests.BulkEditPartAssignmentViewTestCase -v 2`

Expected: PASS

- [ ] **Step 5: Check the picker in the browser**

Open a song that has at least one part. Confirm:

- Bulk Edit Part Assignments sits next to New Part Assignment and opens this page.
- Each part is a section. An instrument already in use is text, with one chip per current member. No readiness or solo text.
- Click the text field, type part of a name, click a person, and they become a chip. That person is no longer in the list.
- × removes a chip. Backspace in an empty field removes the last chip. Backspace with text only deletes text. Clicking outside closes the list.
- Add row shows an instrument dropdown that hides instruments already on that part, including a second new row after one is chosen. Remove row drops only that new row.
- Save returns to the song. A new person is Ready to Perform and cannot solo. A person who was already assigned keeps readiness and solo. A removed person is gone.
- Two rows on one part with the same instrument leave the data unchanged and show the duplicate-instrument error with the chips still there.

- [ ] **Step 6: Commit**

```bash
git add prsb/band/static/band/js/part_assignment_bulk_edit.js prsb/band/static/band/css/style.css prsb/band/templates/band/partassignment_bulk_edit.html prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add the part-assignment chip picker.

Each part can gain a row, and members are added or removed without a checkbox for every person.

EOF
)"
```
