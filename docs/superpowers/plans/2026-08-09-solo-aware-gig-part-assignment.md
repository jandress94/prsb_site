# Solo-Aware Gig Part Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture which song parts have solos and which members can solo on them, then soft-prefer soloists when assigning gig parts without changing song recommendation scores.

**Architecture:** Add `SongPart.has_solo` and `PartAssignment.can_solo` with validation and cleanup. In the MILP objective, reward ordinary assignments where both flags are true with a bonus larger than the song’s max instrument reward and small enough that missing-part penalties still dominate. Surface the fields in admin and existing site forms; mark solos lightly in song and gig assignment UIs.

**Tech Stack:** Django 5, Django TestCase, NumPy/SciPy MILP, Django formsets/templates

## Global Constraints

- Soft preference only: non-soloists may cover a solo part when no soloist is available.
- Priority order: cover part ≫ assign soloist on solo part ≫ scarce instrument fit ≫ song-count / random.
- Song recommendation score remains `get_score(num_missing_parts, instrument_fit)` only — solo fields do not affect ranking.
- `can_solo=True` is allowed only when `song_part.has_solo` is true (model + form validation).
- Clearing `SongPart.has_solo` clears related `PartAssignment.can_solo` flags in that save path.
- No `can_solo` on `GigPartAssignmentOverride`; override variables get no soloist bonus.
- No song-level `has_solo` column; derive when needed via `any(part.has_solo)`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `prsb/band/models.py` | `SongPart.has_solo`, `PartAssignment.can_solo`, `clean()` / `save()` consistency |
| `prsb/band/migrations/0031_songpart_has_solo_partassignment_can_solo.py` | Schema migration (number may bump) |
| `prsb/scripts/gig_part_assignment.py` | Soloist objective bonus in MILP |
| `prsb/band/admin.py` | Expose new fields |
| `prsb/band/views.py` | PartAssignmentForm + Song update formset for `has_solo` |
| `prsb/band/templates/band/partassignment_create_form.html` | `can_solo` enable/disable JS |
| `prsb/band/templates/band/partassignment_update_form.html` | Same JS |
| `prsb/band/templates/band/song_update_form.html` | Parts `has_solo` formset |
| `prsb/band/templates/band/song_detail.html` | Solo markers |
| `prsb/band/templates/band/gig_part_assignments.html` | Solo markers |
| `prsb/band/static/band/css/style.css` | Light solo marker styling |
| `prsb/band/tests.py` | Validation, optimizer, UI tests |

---

### Task 1: Solo fields, validation, and cleanup

**Files:**
- Modify: `prsb/band/models.py`
- Create: `prsb/band/migrations/0031_songpart_has_solo_partassignment_can_solo.py` (via makemigrations)
- Modify: `prsb/band/tests.py`
- Modify: `prsb/band/admin.py` (list/display for the new fields)

**Interfaces:**
- Consumes: existing `SongPart`, `PartAssignment`
- Produces:
  - `SongPart.has_solo: bool` default `False`
  - `PartAssignment.can_solo: bool` default `False`
  - `PartAssignment.clean()` raises `ValidationError` if `can_solo` and not `song_part.has_solo`
  - `SongPart.save()` clears related `can_solo` when `has_solo` is false

- [ ] **Step 1: Write the failing tests**

Add to `prsb/band/tests.py`:

```python
from django.core.exceptions import ValidationError


class SoloFieldsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Solo Fields Song", in_gig_rotation=True)
        cls.solo_part = SongPart.objects.create(song=cls.song, name="Lead", has_solo=True)
        cls.plain_part = SongPart.objects.create(song=cls.song, name="Pad", has_solo=False)
        cls.trumpet = Instrument.objects.create(name="Trumpet SoloFields", order=0)
        cls.user = User.objects.create_user(username="solo_fields_user", first_name="Sam", last_name="Solo")

    def test_defaults_are_false(self):
        part = SongPart.objects.create(song=self.song, name="Default Part")
        self.assertFalse(part.has_solo)
        assignment = PartAssignment(
            member=self.user.bandmember,
            song_part=self.solo_part,
            instrument=self.trumpet,
        )
        self.assertFalse(assignment.can_solo)

    def test_can_solo_rejected_when_part_has_no_solo(self):
        assignment = PartAssignment(
            member=self.user.bandmember,
            song_part=self.plain_part,
            instrument=self.trumpet,
            can_solo=True,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_can_solo_allowed_when_part_has_solo(self):
        assignment = PartAssignment(
            member=self.user.bandmember,
            song_part=self.solo_part,
            instrument=self.trumpet,
            can_solo=True,
            performance_readiness=PerformanceReadiness.READY,
        )
        assignment.full_clean()
        assignment.save()
        self.assertTrue(PartAssignment.objects.get(pk=assignment.pk).can_solo)

    def test_clearing_has_solo_clears_can_solo(self):
        assignment = PartAssignment.objects.create(
            member=self.user.bandmember,
            song_part=self.solo_part,
            instrument=self.trumpet,
            can_solo=True,
            performance_readiness=PerformanceReadiness.READY,
        )
        self.solo_part.has_solo = False
        self.solo_part.save()
        assignment.refresh_from_db()
        self.assertFalse(assignment.can_solo)
        self.assertFalse(SongPart.objects.get(pk=self.solo_part.pk).has_solo)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.SoloFieldsTestCase -v 2
```

Expected: FAIL (fields / methods missing, or `has_solo`/`can_solo` unexpected kwargs).

- [ ] **Step 3: Add model fields and behavior**

In `prsb/band/models.py`:

On `SongPart`, after `name`:

```python
has_solo = models.BooleanField(default=False)
```

Override `save`:

```python
def save(self, *args, **kwargs):
    super().save(*args, **kwargs)
    if not self.has_solo:
        PartAssignment.objects.filter(song_part=self, can_solo=True).update(can_solo=False)
```

On `PartAssignment`, after `performance_readiness`:

```python
can_solo = models.BooleanField(default=False)
```

Add:

```python
def clean(self):
    super().clean()
    if self.can_solo and self.song_part_id and not self.song_part.has_solo:
        raise ValidationError({"can_solo": "Can only solo on a part that has a solo."})
```

(`ValidationError` is already imported at the top of `models.py`.)

- [ ] **Step 4: Migration + admin**

Run:

```bash
cd prsb && poetry run python manage.py makemigrations band --name songpart_has_solo_partassignment_can_solo
```

In `prsb/band/admin.py`:

- `SongPartInline`: leave fields default (new `has_solo` appears) or set `fields = ("name", "has_solo")` if fields are explicit later.
- `PartAssignmentAdmin.list_display`: add `"can_solo"`.
- `PartAssignmentAdmin.list_filter`: add `"can_solo"` and `"song_part__has_solo"`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd prsb && poetry run python manage.py test band.tests.SoloFieldsTestCase -v 2
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add prsb/band/models.py prsb/band/migrations/0031_songpart_has_solo_partassignment_can_solo.py prsb/band/admin.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add has_solo and can_solo fields with validation.

EOF
)"
```

(Adjust migration filename if makemigrations chose a different number.)

---

### Task 2: Prefer soloists in the gig part assignment optimizer

**Files:**
- Modify: `prsb/scripts/gig_part_assignment.py`
- Modify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `PartAssignment.has_solo` via `assignment.song_part.has_solo`, `PartAssignment.can_solo`
- Produces: unchanged `get_gig_song_part_assignments(...) -> tuple[list[PartAssignment] | None, float]` behavior except assignment choice prefers soloists
- Produces: `ScoringConfig` solo reward constant / dynamic missing-part dominance as below
- Does **not** change `get_score`

- [ ] **Step 1: Write the failing optimizer tests**

Add to `prsb/band/tests.py`:

```python
class SoloAwareAssignmentTestCase(TestCase):
    """Optimizer prefers can_solo on has_solo parts without changing score formula."""

    @classmethod
    def setUpTestData(cls):
        cls.trumpet = Instrument.objects.create(name="Trumpet SoloOpt", quantity=2, order=0)
        cls.mellophone = Instrument.objects.create(name="Mellophone SoloOpt", quantity=1, order=1)

        cls.alice = User.objects.create_user(username="solo_opt_alice", first_name="Alice", last_name="A")
        cls.bob = User.objects.create_user(username="solo_opt_bob", first_name="Bob", last_name="B")
        cls.carol = User.objects.create_user(username="solo_opt_carol", first_name="Carol", last_name="C")

    def _gig_with_instruments(self, instruments_and_qty):
        gig = Gig.objects.create(
            name="Solo Opt Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        for instrument, qty in instruments_and_qty:
            GigInstrument.objects.create(gig=gig, instrument=instrument, gig_quantity=qty)
        for member in BandMember.objects.filter(user__username__startswith="solo_opt_"):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
        return gig

    def test_prefers_soloist_over_non_soloist_same_instrument(self):
        song = Song.objects.create(title="Solo Prefer Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=True,
        )
        PartAssignment.objects.create(
            member=self.bob.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        players = setlist[0].part_assignments
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0].member, self.alice.bandmember)

    def test_covers_solo_part_with_non_soloist_when_needed(self):
        song = Song.objects.create(title="Solo Fallback Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.bob.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        self.assertEqual(len(setlist[0].part_assignments), 1)
        self.assertEqual(setlist[0].part_assignments[0].member, self.bob.bandmember)
        self.assertEqual(setlist[0].unplayed_parts, set())

    def test_solo_preference_beats_scarcer_instrument(self):
        # Alice can solo on plentiful trumpet; Bob cannot solo on scarce mellophone.
        song = Song.objects.create(title="Solo Vs Scarcity Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=True,
        )
        PartAssignment.objects.create(
            member=self.bob.bandmember, song_part=lead, instrument=self.mellophone,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 2), (self.mellophone, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        players = setlist[0].part_assignments
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0].member, self.alice.bandmember)
        self.assertEqual(players[0].instrument, self.trumpet)

    def test_covering_part_still_beats_solo_preference(self):
        # Two parts. Alice is the only person who can cover Pad, and can also solo on Lead.
        # Bob can cover Lead but cannot solo. Prefer covering both parts over putting Alice on Lead.
        song = Song.objects.create(title="Cover Beats Solo Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        pad = SongPart.objects.create(song=song, name="Pad", has_solo=False)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=True,
        )
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=pad, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        PartAssignment.objects.create(
            member=self.bob.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 2)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        gpa = setlist[0]
        self.assertEqual(gpa.unplayed_parts, set())
        by_part = {pa.song_part_id: pa.member for pa in gpa.part_assignments}
        self.assertEqual(by_part[pad.id], self.alice.bandmember)
        self.assertEqual(by_part[lead.id], self.bob.bandmember)

    def test_song_score_ignores_solo_fields(self):
        # Same coverage and instruments → same score whether can_solo is set or not.
        song = Song.objects.create(title="Score Unchanged Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        score_without = setlist[0].score

        PartAssignment.objects.filter(song_part=lead).update(can_solo=True)
        setlist2, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist2[0].score, score_without)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prsb && poetry run python manage.py test band.tests.SoloAwareAssignmentTestCase -v 2
```

Expected: FAIL on preference tests (`test_prefers_soloist...`, `test_solo_preference_beats_scarcer_instrument`) while fallback/score tests may already pass.

- [ ] **Step 3: Implement soloist objective bonus**

In `prsb/scripts/gig_part_assignment.py` `ScoringConfig`, document the solo reward. Use a **per-solve** reward so covering always wins:

After computing `c_instrument` / `max_instrument_reward` logic, when building the objective (around the existing `c_missing_part_penalty` block):

```python
# Soloist reward: beat the song's max instrument reward, but keep
# num_parts * soloist_reward < missing-part penalty so covering always wins.
max_instrument_reward = ScoringConfig.SCORE_RANGE / 2 * ScoringConfig.ASSIGNMENT_WEIGHT_INSTRUMENT
soloist_reward = max_instrument_reward + 1
missing_part_penalty = max(
    ScoringConfig.MISSING_PART_PENALTY,
    soloist_reward * max(num_parts, 1) + 1,
)

c_soloist = np.zeros(num_vars)
for i, assignment in enumerate(all_assignments):
    if assignment.can_solo and assignment.song_part.has_solo:
        c_soloist[i] = -soloist_reward
# Override variables (indices num_assignments .. num_assignments+num_overrides-1) stay 0.

c_missing_part_penalty = np.zeros(num_vars)
c_missing_part_penalty[num_assignments + num_overrides:] = missing_part_penalty

c = c_instrument + c_random + c_per_song_penalty + c_soloist + c_missing_part_penalty
```

Remove the old assignment that set `c_missing_part_penalty[...] = ScoringConfig.MISSING_PART_PENALTY` so there is a single definition.

Keep `get_score` and the post-solve `instrument_fit` calculation unchanged (still based only on `c_instrument` / `max_instrument_reward`).

- [ ] **Step 4: Run optimizer tests**

```bash
cd prsb && poetry run python manage.py test band.tests.SoloAwareAssignmentTestCase band.tests.GigRecommendationScoreTestCase band.tests.GigPartAssignmentOverrideTestCase -v 2
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prsb/scripts/gig_part_assignment.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Prefer soloists when assigning solo song parts.

EOF
)"
```

---

### Task 3: Part assignment forms — `can_solo` with part gating

**Files:**
- Modify: `prsb/band/views.py` (`PartAssignmentForm`)
- Modify: `prsb/band/templates/band/partassignment_create_form.html`
- Modify: `prsb/band/templates/band/partassignment_update_form.html`
- Modify: `prsb/band/templates/band/song_detail.html` (assignment table marker)
- Modify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `PartAssignment.can_solo`, `SongPart.has_solo`
- Produces: form field `can_solo`; `clean()` rejects invalid combo; template JS enables checkbox only for solo parts

- [ ] **Step 1: Write the failing form/view tests**

```python
class PartAssignmentCanSoloFormTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Form Solo Song", in_gig_rotation=True)
        cls.solo_part = SongPart.objects.create(song=cls.song, name="Lead", has_solo=True)
        cls.plain_part = SongPart.objects.create(song=cls.song, name="Pad", has_solo=False)
        cls.trumpet = Instrument.objects.create(name="Trumpet FormSolo", order=0)
        cls.user = User.objects.create_user(username="form_solo_user", first_name="Fran", last_name="Form")

    def test_form_rejects_can_solo_on_non_solo_part(self):
        from band.views import PartAssignmentForm
        form = PartAssignmentForm(data={
            "member": self.user.bandmember.pk,
            "song_part": self.plain_part.pk,
            "instrument": self.trumpet.pk,
            "performance_readiness": PerformanceReadiness.READY,
            "can_solo": True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("can_solo", form.errors)

    def test_form_accepts_can_solo_on_solo_part(self):
        from band.views import PartAssignmentForm
        form = PartAssignmentForm(data={
            "member": self.user.bandmember.pk,
            "song_part": self.solo_part.pk,
            "instrument": self.trumpet.pk,
            "performance_readiness": PerformanceReadiness.READY,
            "can_solo": True,
        })
        self.assertTrue(form.is_valid(), form.errors)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prsb && poetry run python manage.py test band.tests.PartAssignmentCanSoloFormTestCase -v 2
```

Expected: FAIL (`can_solo` not a form field / unexpected).

- [ ] **Step 3: Update `PartAssignmentForm`**

In `prsb/band/views.py`:

```python
class PartAssignmentForm(forms.ModelForm):
    class Meta:
        model = PartAssignment
        fields = ['member', 'song_part', 'instrument', 'performance_readiness', 'can_solo']

    def __init__(self, *args, **kwargs):
        # ... existing member_id / song_id logic unchanged ...
        # After queryset setup, annotate options for the template/JS:
        self.fields['song_part'].queryset = self.fields['song_part'].queryset.select_related('song')

    def clean(self):
        cleaned = super().clean()
        song_part = cleaned.get('song_part')
        can_solo = cleaned.get('can_solo')
        if can_solo and song_part is not None and not song_part.has_solo:
            self.add_error('can_solo', 'Can only solo on a part that has a solo.')
        if song_part is not None and not song_part.has_solo:
            cleaned['can_solo'] = False
        return cleaned
```

Keep the existing `__init__` member/song branching; only extend `Meta.fields` and add `clean` (and `select_related` if useful).

- [ ] **Step 4: Template JS to enable `can_solo` only for solo parts**

In both `partassignment_create_form.html` and `partassignment_update_form.html`, after the form, add a script that:

1. Builds a set of song-part IDs with `has_solo` from option `data-has-solo` attributes **or** from a JSON map embedded in the page.
2. On `song_part` change (and on load), enables `#id_can_solo` only when the selected part has a solo; otherwise unchecks and disables it.

Because Django’s default `<select>` options lack data attributes, either:

**Option A (preferred):** In `PartAssignmentForm.__init__`, replace the song_part widget choices with labeled options via a custom widget, **or** simpler:

**Option B:** Embed in the template:

```django
{{ form.as_p }}
<script id="solo-parts-data" type="application/json">
  [{% for part in form.fields.song_part.queryset %}{% if part.has_solo %}{{ part.pk }},{% endif %}{% endfor %}]
</script>
```

(Use a view context list if iterating queryset in template is awkward — pass `solo_part_ids` from the form as `form.solo_part_ids`.)

Add on the form:

```python
@property
def solo_part_ids(self):
    return list(self.fields['song_part'].queryset.filter(has_solo=True).values_list('pk', flat=True))
```

Template:

```html
<form method="post">{% csrf_token %}
    {{ form.as_p }}
    <input type="submit" value="...">
</form>
<script>
(function () {
    const soloPartIds = new Set({{ form.solo_part_ids|safe }});  // render as JSON list
    const partSelect = document.getElementById("id_song_part");
    const canSolo = document.getElementById("id_can_solo");
    function sync() {
        const allowed = soloPartIds.has(Number(partSelect.value));
        canSolo.disabled = !allowed;
        if (!allowed) canSolo.checked = false;
    }
    partSelect.addEventListener("change", sync);
    sync();
})();
</script>
```

Render `solo_part_ids` safely with Django’s `json_script` filter:

```html
{{ form.solo_part_ids|json_script:"solo-part-ids" }}
<script>
(function () {
    const soloPartIds = new Set(JSON.parse(document.getElementById("solo-part-ids").textContent));
    ...
})();
</script>
```

Make `solo_part_ids` a list on the form instance in `__init__`: `self.solo_part_ids = list(...)`.

- [ ] **Step 5: Song detail assignment markers**

In `song_detail.html` parts list:

```django
<li>{{ part.name }}{% if part.has_solo %} <span class="solo-marker" title="Has solo">solo</span>{% endif %}</li>
```

In the assignments table, add a column or suffix when `part_assignment.can_solo`:

```django
<td>{{ part_assignment.member.user.get_full_name }}{% if part_assignment.can_solo %} <span class="solo-marker" title="Can solo">solo</span>{% endif %}</td>
```

Add light CSS in `style.css`:

```css
.solo-marker {
    font-size: 0.85em;
    font-style: italic;
    opacity: 0.85;
}
```

- [ ] **Step 6: Run form tests**

```bash
cd prsb && poetry run python manage.py test band.tests.PartAssignmentCanSoloFormTestCase -v 2
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add prsb/band/views.py prsb/band/templates/band/partassignment_create_form.html prsb/band/templates/band/partassignment_update_form.html prsb/band/templates/band/song_detail.html prsb/band/static/band/css/style.css prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Gate can_solo on part-assignment forms and show solo markers.

EOF
)"
```

---

### Task 4: Song update formset for `has_solo`

**Files:**
- Modify: `prsb/band/views.py` (`SongUpdateView`)
- Modify: `prsb/band/templates/band/song_update_form.html`
- Modify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: existing `SongPart` rows for a song
- Produces: on song update POST, each part’s `has_solo` can be toggled; clearing it clears `can_solo` via `SongPart.save()`

- [ ] **Step 1: Write the failing view test**

```python
class SongHasSoloUpdateTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Update Solo Song", composer="X", in_gig_rotation=True)
        cls.part = SongPart.objects.create(song=cls.song, name="Lead", has_solo=False)
        cls.trumpet = Instrument.objects.create(name="Trumpet SongSolo", order=0)
        cls.user = User.objects.create_user(username="song_solo_user", first_name="Sue", last_name="Song")
        PartAssignment.objects.create(
            member=cls.user.bandmember,
            song_part=cls.part,
            instrument=cls.trumpet,
            performance_readiness=PerformanceReadiness.READY,
            can_solo=False,
        )

    def test_song_update_can_set_has_solo(self):
        url = reverse("band:song_update", kwargs={"pk": self.song.pk})
        # GET to obtain formset management data
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # POST song fields + formset — adjust management form keys to match implementation
        data = {
            "title": self.song.title,
            "composer": self.song.composer,
            "duration": "",
            "in_gig_rotation": "on",
            "form": self.song.form,
            "parts-TOTAL_FORMS": "1",
            "parts-INITIAL_FORMS": "1",
            "parts-MIN_NUM_FORMS": "0",
            "parts-MAX_NUM_FORMS": "1000",
            "parts-0-id": str(self.part.pk),
            "parts-0-has_solo": "on",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.part.refresh_from_db()
        self.assertTrue(self.part.has_solo)
```

(If the site requires login, use `self.client.force_login` with a staff/superuser created in setup — match whatever other view tests do. If none exist, create `User.objects.create_superuser(...)` and force_login.)

- [ ] **Step 2: Run test to verify it fails**

```bash
cd prsb && poetry run python manage.py test band.tests.SongHasSoloUpdateTestCase -v 2
```

Expected: FAIL (no formset / has_solo not saved).

- [ ] **Step 3: Implement formset on `SongUpdateView`**

In `prsb/band/views.py`:

```python
from django.forms import inlineformset_factory

SongPartSoloFormSet = inlineformset_factory(
    Song,
    SongPart,
    fields=("has_solo",),
    extra=0,
    can_delete=False,
)


class SongUpdateView(generic.UpdateView):
    model = Song
    template_name_suffix = "_update_form"
    fields = ["title", "composer", "duration", "in_gig_rotation", "form"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context["parts_formset"] = SongPartSoloFormSet(self.request.POST, instance=self.object)
        else:
            context["parts_formset"] = SongPartSoloFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        formset = context["parts_formset"]
        if formset.is_valid():
            self.object = form.save()
            formset.instance = self.object
            formset.save()  # each SongPart.save() clears can_solo when has_solo is false
            return redirect(self.get_success_url())
        return self.render_to_response(self.get_context_data(form=form))
```

Use the project’s existing redirect/success_url pattern for `SongUpdateView` (if it relies on `get_absolute_url`, keep that).

- [ ] **Step 4: Update `song_update_form.html`**

```django
<form method="post">{% csrf_token %}
    {{ form.as_p }}
    <h2>Parts</h2>
    {{ parts_formset.management_form }}
    <table>
        <thead><tr><th>Part</th><th>Has solo</th></tr></thead>
        <tbody>
        {% for f in parts_formset %}
            <tr>
                <td>{{ f.instance.name }}{{ f.id }}</td>
                <td>{{ f.has_solo }}</td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    <input type="submit" value="Update">
</form>
```

Prefix must match the formset (`parts` if `SongPartSoloFormSet` uses `prefix="parts"` — set `prefix="parts"` when constructing the formset in the view for stable test keys).

```python
SongPartSoloFormSet(self.request.POST, instance=self.object, prefix="parts")
```

- [ ] **Step 5: Run the song update test**

```bash
cd prsb && poetry run python manage.py test band.tests.SongHasSoloUpdateTestCase -v 2
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add prsb/band/views.py prsb/band/templates/band/song_update_form.html prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Edit part has_solo flags on the song update page.

EOF
)"
```

---

### Task 5: Gig part assignment UI solo markers

**Files:**
- Modify: `prsb/band/templates/band/gig_part_assignments.html`
- Modify: `prsb/band/tests.py` (optional light content assertion)

**Interfaces:**
- Consumes: `song_part.has_solo`, `part_assignment.can_solo` on rendered GPA rows
- Produces: visible “solo” markers next to solo parts and soloist assignees; scores unchanged

- [ ] **Step 1: Write a focused render test**

Add `GigPartAssignmentSoloMarkerTestCase` in `prsb/band/tests.py` with a setlist song that has `has_solo=True` and an assignee with `can_solo=True` (same fixture pattern as `SoloAwareAssignmentTestCase.test_prefers_soloist_over_non_soloist_same_instrument`), then:

```python
class GigPartAssignmentSoloMarkerTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.trumpet = Instrument.objects.create(name="Trumpet Marker", quantity=1, order=0)
        cls.alice = User.objects.create_user(username="solo_marker_alice", first_name="Alice", last_name="M")
        cls.song = Song.objects.create(title="Solo Marker Song", in_gig_rotation=True)
        cls.lead = SongPart.objects.create(song=cls.song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=cls.alice.bandmember,
            song_part=cls.lead,
            instrument=cls.trumpet,
            performance_readiness=PerformanceReadiness.READY,
            can_solo=True,
        )
        cls.gig = Gig.objects.create(
            name="Solo Marker Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.trumpet, gig_quantity=1)
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.alice.bandmember, status=GigAttendance.AVAILABLE,
        )
        GigSetlistEntry.objects.create(gig=cls.gig, song=cls.song)

    def test_gig_part_assignments_mark_solos(self):
        response = self.client.get(
            reverse("band:gig_part_assignments_detail", kwargs={"pk": self.gig.pk})
        )
        self.assertContains(response, "solo-marker")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd prsb && poetry run python manage.py test band.tests.GigPartAssignmentSoloMarkerTestCase -v 2
```

Expected: FAIL (marker absent).

- [ ] **Step 3: Update template**

In both setlist and recommendations tables in `gig_part_assignments.html`, keep the existing part string and append the marker:

```django
{{ part_assignment.song_part }}{% if part_assignment.song_part.has_solo %} <span class="solo-marker">solo</span>{% endif %}
```

For unplayed parts:

```django
{{ song_part }}{% if song_part.has_solo %} <span class="solo-marker">solo</span>{% endif %}
```

For members:

```django
{{ part_assignment.member }}{% if part_assignment.can_solo and part_assignment.song_part.has_solo %} <span class="solo-marker">solo</span>{% endif %}
```

Apply the same pattern in both Setlist and Recommendations loops (unplayed-part loops + played-part loops + member loops).

- [ ] **Step 4: Run test**

```bash
cd prsb && poetry run python manage.py test band.tests.GigPartAssignmentSoloMarkerTestCase -v 2
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prsb/band/templates/band/gig_part_assignments.html prsb/band/tests.py prsb/band/static/band/css/style.css
git commit -m "$(cat <<'EOF'
Mark solo parts and soloists on gig part assignments.

EOF
)"
```

---

### Task 6: Full verification

**Files:**
- Verify: all files touched above

- [ ] **Step 1: Run the full band test suite**

```bash
cd prsb && poetry run python manage.py test band.tests -v 2
```

Expected: all tests PASS

- [ ] **Step 2: Spec checklist (manual)**

Confirm each success criterion from `docs/superpowers/specs/2026-08-09-solo-aware-gig-part-assignment-design.md`:

- [ ] `has_solo` / `can_solo` stored and validated
- [ ] Soloist preferred; non-soloist fallback works
- [ ] Solo beats scarcity; covering beats solo
- [ ] Recommendation scores unchanged by solo fields
- [ ] Admin + site forms editable
- [ ] Markers visible on song detail and gig assignments

- [ ] **Step 3: Final commit if any leftover fixes**

Only if Step 1/2 required code changes; otherwise skip.

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| `SongPart.has_solo` | 1 |
| `PartAssignment.can_solo` + validation | 1, 3 |
| Clear `can_solo` when `has_solo` cleared | 1 (save), 4 (formset uses save) |
| Soft soloist preference in MILP | 2 |
| Solo ≫ scarcity; cover ≫ solo | 2 |
| Score unchanged | 2 |
| No override `can_solo` | 2 (explicit) |
| Admin fields | 1 |
| Site part-assignment `can_solo` | 3 |
| Site song update `has_solo` | 4 |
| Song detail markers | 3 |
| Gig assignment markers | 5 |
| Full suite | 6 |
