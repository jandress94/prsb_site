# Drum Kit Fallback Players Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically inject priority-ordered drum-kit cover candidates into gig part assignment when a song’s listed kit player is not `AVAILABLE`, without per-song overrides or hardcoded instrument/part IDs.

**Architecture:** Flag the target kit instrument on `Instrument`, store cover-pool members with priorities in `DrumKitCoverPlayer`, and before each song’s MILP solve inject unsaved READY `PartAssignment`s for the best available cover tier when no listed kit player is attending. Song part identity comes from existing kit repertoire rows on that song.

**Tech Stack:** Django 5, Django TestCase, NumPy/SciPy MILP (`scripts/gig_part_assignment.py`)

## Global Constraints

- Listed kit player always wins when `AVAILABLE`; covers inject only when none are.
- Cover pool is explicit (`DrumKitCoverPlayer`); knowing a kit song does not imply pool membership.
- Best available priority tier only (lower `priority` = preferred); equal priority left to existing MILP tie-breaks.
- `NOT_PLAYING` is applied before choosing the priority tier (so Jansen blocked → Carlos injects).
- Overrides win: skip injecting a member who already has an `ASSIGN` override on the song; `ASSIGN`/`NOT_PLAYING` behave as today in the MILP.
- Solver must not hardcode instrument/part PKs or name strings; resolve kit instrument via `is_drum_kit_cover_instrument`.
- `maybe_available` listed players do not count as present; cover members must be `AVAILABLE` to inject.
- Seed/migration may use the name `"Drum Set"` once to set the flag; solver logic must not.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `prsb/band/models.py` | `Instrument.is_drum_kit_cover_instrument`, `DrumKitCoverPlayer` |
| `prsb/band/migrations/0032_*.py` | Schema (+ optional data) migration |
| `prsb/band/admin.py` | Admin for new field + model |
| `prsb/scripts/gig_part_assignment.py` | Cover injection helper + wire into `get_gig_part_assignments` |
| `prsb/band/tests.py` | Model + injection + integration tests |
| `resources/data/Instruments.csv` | Optional column / note for kit flag on fresh seed |
| `resources/data/DrumKitCoverPlayers.csv` | Seed cover pool (Jansen/Ben/Carlos) |
| `prsb/scripts/create_db_objects.py` | Load kit flag + cover players on fresh DB |
| `README.md` | Remove Ideas Log bullet once feature lands |

---

### Task 1: Data model and admin

**Files:**
- Modify: `prsb/band/models.py`
- Create: `prsb/band/migrations/0032_instrument_is_drum_kit_cover_instrument_drumkitcoverplayer.py` (via `makemigrations`; number may bump)
- Modify: `prsb/band/admin.py`
- Modify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: existing `Instrument`, `BandMember`
- Produces:
  - `Instrument.is_drum_kit_cover_instrument: bool` default `False`
  - `DrumKitCoverPlayer(member: BandMember, priority: int)` with `member` unique; `Meta.ordering = ["priority", "member__user__first_name"]`
  - `DrumKitCoverPlayer.__str__` like `"Jansen Leggett (priority 1)"`

- [ ] **Step 1: Write the failing tests**

Add to `prsb/band/tests.py` (extend imports for `DrumKitCoverPlayer`):

```python
class DrumKitCoverModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kit = Instrument.objects.create(
            name="KitCoverModel Drum Set", order=0, is_drum_kit_cover_instrument=True,
        )
        cls.other = Instrument.objects.create(name="KitCoverModel Congas", order=1)
        cls.user = User.objects.create_user(
            username="kit_cover_model_user", first_name="Kit", last_name="Cover",
        )

    def test_instrument_flag_default_false(self):
        inst = Instrument.objects.create(name="KitCoverModel Default", order=2)
        self.assertFalse(inst.is_drum_kit_cover_instrument)

    def test_drum_kit_cover_player_unique_member_and_str(self):
        row = DrumKitCoverPlayer.objects.create(
            member=self.user.bandmember, priority=1,
        )
        self.assertEqual(str(row), "Kit Cover (priority 1)")
        dup = DrumKitCoverPlayer(member=self.user.bandmember, priority=2)
        with self.assertRaises(Exception):
            dup.save()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prsb && poetry run python manage.py test band.tests.DrumKitCoverModelTestCase`

Expected: FAIL (field/model missing)

- [ ] **Step 3: Implement models**

In `prsb/band/models.py`, on `Instrument` after `include_in_coverage_risk`:

```python
is_drum_kit_cover_instrument = models.BooleanField(
    default=False,
    help_text="If checked, this instrument is the target for drum-kit cover fallbacks "
              "(expect exactly one instrument flagged).",
)
```

Add after `BandMember` (or near other config models):

```python
class DrumKitCoverPlayer(models.Model):
    member = models.OneToOneField(BandMember, on_delete=models.CASCADE)
    priority = models.PositiveSmallIntegerField(
        help_text="Lower number = preferred cover (e.g. 1 before 2).",
    )

    class Meta:
        ordering = ["priority", "member__user__first_name", "member__user__last_name"]

    def __str__(self):
        return f"{self.member} (priority {self.priority})"
```

Use `OneToOneField` (unique member) or `ForeignKey` + `UniqueConstraint` — prefer `OneToOneField` for clarity.

- [ ] **Step 4: Makemigrations and migrate**

Run:

```bash
cd prsb && poetry run python manage.py makemigrations band --name instrument_is_drum_kit_cover_instrument_drumkitcoverplayer
cd prsb && poetry run python manage.py migrate
```

- [ ] **Step 5: Admin**

In `prsb/band/admin.py`:

- Import `DrumKitCoverPlayer`
- Add `is_drum_kit_cover_instrument` to `InstrumentAdmin.list_display` (and optionally `list_filter`)
- Register:

```python
class DrumKitCoverPlayerAdmin(admin.ModelAdmin):
    list_display = [band_member_name, "priority"]
    list_filter = ["priority"]
    ordering = ["priority"]

admin.site.register(DrumKitCoverPlayer, DrumKitCoverPlayerAdmin)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd prsb && poetry run python manage.py test band.tests.DrumKitCoverModelTestCase`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add prsb/band/models.py prsb/band/migrations/0032_*.py prsb/band/admin.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add DrumKitCoverPlayer model and kit instrument flag.

EOF
)"
```

---

### Task 2: Cover injection helper (unit-tested)

**Files:**
- Modify: `prsb/scripts/gig_part_assignment.py`
- Modify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `Song`, kit `Instrument`, iterable of `AVAILABLE` `BandMember`s, song-scoped override list, `DrumKitCoverPlayer` queryset/list, kit repertoire `PartAssignment`s for the song
- Produces: `inject_drum_kit_cover_assignments(...) -> list[PartAssignment]` (unsaved READY rows, possibly empty)

Exact signature to implement:

```python
def inject_drum_kit_cover_assignments(
    song: Song,
    kit_instrument: Instrument | None,
    available_members: set[BandMember] | set,
    song_overrides: list[GigPartAssignmentOverride],
    cover_players: list,  # DrumKitCoverPlayer rows
    kit_repertoire: list[PartAssignment],  # all PartAssignments on this song for kit_instrument (any attendance)
) -> list[PartAssignment]:
```

Behavior (must match spec):

1. If `kit_instrument` is `None` or `kit_repertoire` empty → `[]`
2. Partition overrides; build `not_playing` set of `(member_id, song_part_id, instrument_id)` and `assign_member_ids` for members with `ASSIGN` on this song
3. Attachment part = `kit_repertoire[0].song_part` (all kit rows on a song share Engine Room; if multiple parts ever appear, use the part from the repertoire rows — assert/prefer the unique part among `kit_repertoire`)
4. Listed available = members in `kit_repertoire` who are in `available_members`, readiness ≠ `NOT_READY`, and `(member_id, song_part_id, kit_instrument_id)` not in `not_playing`
5. If any listed available → `[]`
6. Eligible covers = `cover_players` whose `member` is in `available_members`, not in `assign_member_ids`, not already a listed *repertoire* kit member on this song (optional skip), and not `not_playing` on `(member, attachment_part, kit_instrument)`
7. If none eligible → `[]`
8. Keep only min `priority` tier
9. Return unsaved `PartAssignment(member=..., song_part=attachment, instrument=kit_instrument, performance_readiness=READY, can_solo=False)` per remaining cover

- [ ] **Step 1: Write the failing tests**

```python
from scripts.gig_part_assignment import inject_drum_kit_cover_assignments


class DrumKitCoverInjectionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Kit Inject Song", in_gig_rotation=True)
        cls.engine = SongPart.objects.create(song=cls.song, name="Engine Room")
        cls.lead = SongPart.objects.create(song=cls.song, name="Lead")
        cls.kit = Instrument.objects.create(
            name="KitInject Drum Set", order=0, is_drum_kit_cover_instrument=True,
        )
        cls.lead_inst = Instrument.objects.create(name="KitInject Lead", order=1)

        cls.ben = User.objects.create_user(username="kit_ben", first_name="Ben", last_name="K")
        cls.jansen = User.objects.create_user(username="kit_jansen", first_name="Jansen", last_name="K")
        cls.carlos = User.objects.create_user(username="kit_carlos", first_name="Carlos", last_name="K")

        PartAssignment.objects.create(
            member=cls.ben.bandmember, song_part=cls.engine, instrument=cls.kit,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.jansen.bandmember, song_part=cls.lead, instrument=cls.lead_inst,
            performance_readiness=PerformanceReadiness.READY,
        )

        DrumKitCoverPlayer.objects.create(member=cls.jansen.bandmember, priority=1)
        DrumKitCoverPlayer.objects.create(member=cls.ben.bandmember, priority=2)
        DrumKitCoverPlayer.objects.create(member=cls.carlos.bandmember, priority=2)

        cls.kit_repertoire = list(
            PartAssignment.objects.filter(song_part__song=cls.song, instrument=cls.kit)
        )
        cls.covers = list(DrumKitCoverPlayer.objects.all())

    def _inject(self, available, overrides=None):
        return inject_drum_kit_cover_assignments(
            song=self.song,
            kit_instrument=self.kit,
            available_members=set(available),
            song_overrides=overrides or [],
            cover_players=self.covers,
            kit_repertoire=self.kit_repertoire,
        )

    def test_no_inject_when_listed_player_available(self):
        result = self._inject([self.ben.bandmember, self.jansen.bandmember, self.carlos.bandmember])
        self.assertEqual(result, [])

    def test_injects_best_priority_when_listed_absent(self):
        result = self._inject([self.jansen.bandmember, self.carlos.bandmember])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].member, self.jansen.bandmember)
        self.assertEqual(result[0].song_part, self.engine)
        self.assertEqual(result[0].instrument, self.kit)
        self.assertEqual(result[0].performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(result[0].pk)  # unsaved

    def test_not_playing_falls_through_to_next_tier(self):
        gig = Gig.objects.create(
            name="Kit Inject Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
        )
        gi = GigInstrument.objects.create(gig=gig, instrument=self.kit, gig_quantity=1)
        override = GigPartAssignmentOverride(
            member=self.jansen.bandmember,
            song_part=self.engine,
            gig_instrument=gi,
            override_type=OverrideType.NOT_PLAYING,
        )
        result = self._inject(
            [self.jansen.bandmember, self.carlos.bandmember],
            overrides=[override],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].member, self.carlos.bandmember)

    def test_maybe_available_listed_player_does_not_block_covers(self):
        # Ben not in available_members (maybe/unavailable) → covers inject
        result = self._inject([self.jansen.bandmember])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].member, self.jansen.bandmember)

    def test_no_kit_instrument_returns_empty(self):
        result = inject_drum_kit_cover_assignments(
            song=self.song,
            kit_instrument=None,
            available_members={self.jansen.bandmember},
            song_overrides=[],
            cover_players=self.covers,
            kit_repertoire=self.kit_repertoire,
        )
        self.assertEqual(result, [])

    def test_skip_cover_with_assign_override_on_song(self):
        gig = Gig.objects.create(
            name="Kit Assign Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
        )
        gi_lead = GigInstrument.objects.create(gig=gig, instrument=self.lead_inst, gig_quantity=1)
        override = GigPartAssignmentOverride(
            member=self.jansen.bandmember,
            song_part=self.lead,
            gig_instrument=gi_lead,
            override_type=OverrideType.ASSIGN,
            performance_readiness=PerformanceReadiness.READY,
        )
        result = self._inject(
            [self.jansen.bandmember, self.carlos.bandmember],
            overrides=[override],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].member, self.carlos.bandmember)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prsb && poetry run python manage.py test band.tests.DrumKitCoverInjectionTestCase`

Expected: FAIL (`inject_drum_kit_cover_assignments` missing)

- [ ] **Step 3: Implement helper**

In `prsb/scripts/gig_part_assignment.py`, import `DrumKitCoverPlayer` only if needed for typing; implement `inject_drum_kit_cover_assignments` using `_partition_overrides` already in the file. Do not reference instrument/part name strings.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prsb && poetry run python manage.py test band.tests.DrumKitCoverInjectionTestCase`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prsb/scripts/gig_part_assignment.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add drum kit cover injection helper.

EOF
)"
```

---

### Task 3: Wire injection into `get_gig_part_assignments`

**Files:**
- Modify: `prsb/scripts/gig_part_assignment.py`
- Modify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `inject_drum_kit_cover_assignments`, gig instruments, attendees, overrides, `DrumKitCoverPlayer`, kit repertoire query
- Produces: `get_gig_part_assignments` appends injected covers into each song’s candidate list before `get_gig_song_part_assignments`

Wiring sketch inside `get_gig_part_assignments` (after building `song_to_part_assignments` / attendees / overrides):

```python
kit_instrument = (
    Instrument.objects.filter(is_drum_kit_cover_instrument=True).order_by("order").first()
)
gig_has_kit = kit_instrument is not None and any(
    gi.instrument_id == kit_instrument.id for gi in gig_instruments
)
cover_players = list(DrumKitCoverPlayer.objects.select_related("member__user")) if gig_has_kit else []
available_member_set = set(attendees)

# Prefetch kit repertoire for rotation songs (no attendance filter)
kit_repertoire_by_song = {}
if gig_has_kit:
    kit_pas = PartAssignment.objects.filter(
        instrument=kit_instrument,
        song_part__song__in_gig_rotation=True,
    ).select_related("member", "song_part", "instrument")
    for pa in kit_pas:
        kit_repertoire_by_song.setdefault(pa.song_part.song_id, []).append(pa)

# Inside the per-song loop, before get_gig_song_part_assignments:
candidates = list(attendee_part_assignments)
if gig_has_kit:
    injected = inject_drum_kit_cover_assignments(
        song=song,
        kit_instrument=kit_instrument,
        available_members=available_member_set,
        song_overrides=song_to_overrides.get(song, []),
        cover_players=cover_players,
        kit_repertoire=kit_repertoire_by_song.get(song.id, []),
    )
    candidates.extend(injected)

part_assignments, score = get_gig_song_part_assignments(
    part_list, candidates, gig_instruments, member_song_counts, song_to_overrides.get(song, []),
)
```

Import `Instrument` and `DrumKitCoverPlayer` in `gig_part_assignment.py`.

- [ ] **Step 1: Write the failing integration test**

```python
class DrumKitCoverGigAssignmentTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Kit Gig Song", in_gig_rotation=True)
        cls.engine = SongPart.objects.create(song=cls.song, name="Engine Room")
        cls.lead_part = SongPart.objects.create(song=cls.song, name="Lead")

        cls.kit = Instrument.objects.create(
            name="KitGig Drum Set", order=0, is_drum_kit_cover_instrument=True,
            include_in_gig_song_count=True,
        )
        cls.lead = Instrument.objects.create(name="KitGig Lead", order=1)

        cls.ben = User.objects.create_user(username="kitgig_ben", first_name="Ben", last_name="G")
        cls.jansen = User.objects.create_user(username="kitgig_jansen", first_name="Jansen", last_name="G")
        cls.carlos = User.objects.create_user(username="kitgig_carlos", first_name="Carlos", last_name="G")
        cls.lead_player = User.objects.create_user(username="kitgig_lead", first_name="Lead", last_name="G")

        PartAssignment.objects.create(
            member=cls.ben.bandmember, song_part=cls.engine, instrument=cls.kit,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.jansen.bandmember, song_part=cls.lead_part, instrument=cls.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.lead_player.bandmember, song_part=cls.lead_part, instrument=cls.lead,
            performance_readiness=PerformanceReadiness.READY,
        )

        DrumKitCoverPlayer.objects.create(member=cls.jansen.bandmember, priority=1)
        DrumKitCoverPlayer.objects.create(member=cls.carlos.bandmember, priority=2)

        cls.gig = Gig.objects.create(
            name="Kit Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.kit, gig_quantity=1)
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.lead, gig_quantity=2)

        for user in (cls.jansen, cls.carlos, cls.lead_player):
            GigAttendance.objects.create(
                gig=cls.gig, member=user.bandmember, status=GigAttendance.AVAILABLE,
            )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.ben.bandmember, status=GigAttendance.UNAVAILABLE,
        )
        GigSetlistEntry.objects.create(gig=cls.gig, song=cls.song)

    def test_jansen_covers_kit_when_ben_unavailable(self):
        setlist, _, _ = get_gig_part_assignments(self.gig, [])
        self.assertEqual(len(setlist), 1)
        kit_players = [
            pa for pa in setlist[0].part_assignments if pa.instrument_id == self.kit.id
        ]
        self.assertEqual(len(kit_players), 1)
        self.assertEqual(kit_players[0].member, self.jansen.bandmember)
        self.assertEqual(len(setlist[0].unplayed_parts), 0)

    def test_listed_ben_used_when_available(self):
        GigAttendance.objects.filter(gig=self.gig, member=self.ben.bandmember).update(
            status=GigAttendance.AVAILABLE,
        )
        setlist, _, _ = get_gig_part_assignments(self.gig, [])
        kit_players = [
            pa for pa in setlist[0].part_assignments if pa.instrument_id == self.kit.id
        ]
        self.assertEqual(len(kit_players), 1)
        self.assertEqual(kit_players[0].member, self.ben.bandmember)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prsb && poetry run python manage.py test band.tests.DrumKitCoverGigAssignmentTestCase`

Expected: FAIL (Engine Room missing / no Jansen on kit) before wiring

- [ ] **Step 3: Wire helper into `get_gig_part_assignments`**

Implement the sketch above. Keep behavior unchanged when no instrument is flagged or the gig has no kit `GigInstrument`.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.DrumKitCoverGigAssignmentTestCase band.tests.DrumKitCoverInjectionTestCase band.tests.GigPartAssignmentOverrideTestCase
```

Expected: PASS (no regressions on overrides)

- [ ] **Step 5: Commit**

```bash
git add prsb/scripts/gig_part_assignment.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Wire drum kit covers into gig part assignment.

EOF
)"
```

---

### Task 4: Seed data, data migration, README

**Files:**
- Create: `resources/data/DrumKitCoverPlayers.csv`
- Modify: `resources/data/Instruments.csv` (add `IsDrumKitCoverInstrument` column) **or** set flag in `create_db_objects.py` / data migration by name once
- Modify: `prsb/scripts/create_db_objects.py`
- Modify: migration from Task 1 **or** add `0033_...` data migration
- Modify: `README.md` (remove Ideas Log bullet)

**Seed CSV** `resources/data/DrumKitCoverPlayers.csv`:

```csv
First Name,Last Name,Priority
Jansen,Leggett,1
Ben,Hills,2
Carlos,Fresnillo,2
```

- [ ] **Step 1: Extend `create_db_objects.py`**

- When creating instruments, set `is_drum_kit_cover_instrument=True` for the row whose name is `Drum Set` (seed-time name lookup only).
- Add `get_drum_kit_cover_players(band_members) -> list[DrumKitCoverPlayer]` reading the CSV via the existing first-name member lookup pattern.
- Bulk-create those rows in `main` / create path alongside other objects.

- [ ] **Step 2: Data migration for existing DBs**

In a RunPython migration (can be same 0032 if not yet applied elsewhere, else new 0033):

```python
def forwards(apps, schema_editor):
    Instrument = apps.get_model("band", "Instrument")
    BandMember = apps.get_model("band", "BandMember")
    DrumKitCoverPlayer = apps.get_model("band", "DrumKitCoverPlayer")
    User = apps.get_model("auth", "User")

    Instrument.objects.filter(name="Drum Set").update(is_drum_kit_cover_instrument=True)

    priorities = {
        ("Jansen", "Leggett"): 1,
        ("Ben", "Hills"): 2,
        ("Carlos", "Fresnillo"): 2,
    }
    for (first, last), priority in priorities.items():
        user = User.objects.filter(first_name=first, last_name=last).first()
        if user is None:
            continue
        member = BandMember.objects.filter(pk=user.pk).first()
        if member is None:
            continue
        DrumKitCoverPlayer.objects.update_or_create(
            member=member, defaults={"priority": priority},
        )
```

Empty `backwards` that clears the flag and deletes seeded cover rows is fine.

- [ ] **Step 3: Remove Ideas Log bullet**

In `README.md`, delete the line:

`- Handling default or fallback drum kit players.`

(Keep any unrelated local README edits out of this commit if they are not part of this feature.)

- [ ] **Step 4: Run full band tests**

Run: `cd prsb && poetry run python manage.py test band.tests`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add resources/data/DrumKitCoverPlayers.csv resources/data/Instruments.csv prsb/scripts/create_db_objects.py prsb/band/migrations/ README.md
git commit -m "$(cat <<'EOF'
Seed drum kit cover players and clear the ideas-log item.

EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| `Instrument.is_drum_kit_cover_instrument` | 1 |
| `DrumKitCoverPlayer` + admin | 1, 4 |
| Primary wins if available | 2, 3 |
| Best priority tier injection | 2, 3 |
| `NOT_PLAYING` fallthrough | 2 |
| `ASSIGN` skip / overrides win | 2, 3 |
| No hardcoded IDs/names in solver | 2, 3 |
| `maybe_available` listed → covers | 2, 3 |
| Attach to repertoire SongPart without cover having Engine Room row | 2, 3 |
| Seed Jansen/Ben/Carlos priorities | 4 |
| Remove README idea | 4 |
| Tests listed in spec | 1–3 |

## Placeholder / consistency review

- Function name fixed as `inject_drum_kit_cover_assignments` across tasks.
- Migration number `0032` may bump if another migration lands first — use whatever `makemigrations` produces.
- No TBD/TODO placeholders remain.
