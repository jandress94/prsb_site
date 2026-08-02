# Coverage Risk Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Reports hub and Coverage Risk report that ranks rotation songs/parts by attendance-weighted coverage fragility for rehearsal prioritization.

**Architecture:** Add `Gig.is_small_group` for attendance lookback filtering. Pure scoring in `prsb/scripts/coverage_risk.py` computes attendance rates and effective coverage on request (no score tables). Thin Django views/templates under `/reports/` render an expandable song list.

**Tech Stack:** Django 5, Django TestCase, Django templates (`<details>` for expand/collapse), existing `band` models.

## Global Constraints

- Only `in_gig_rotation=True` songs
- Lookback: last N **past** full-band gigs (`is_small_group=False`, `start_datetime <= now`); default N=10
- Attendance rate = (# available) / D where D = lookback gig count; zero gigs → rate 1.0 for all
- Readiness weights: ready=1.0, backup=0.5; `not_ready` ignored
- One scoring contribution per (member, song_part) using best readiness; display still lists each instrument assignment
- Song risk = max part risk; `part_risk = 1 / (effective_coverage + 0.01)`
- Unassigned-on-song: active members (`user__is_active=True`) with zero assignments on that song
- No learner suggestions, months lookback, or per-gig risk in v1

---

## File Structure

| File | Responsibility |
|------|----------------|
| `prsb/band/models.py` | Add `Gig.is_small_group` |
| `prsb/band/migrations/0029_gig_is_small_group.py` | Migration (number may bump if 0029 exists) |
| `prsb/band/admin.py` | Show/filter `is_small_group` on Gig admin |
| `prsb/band/views.py` | GigForm field; Reports + CoverageRisk views |
| `prsb/band/urls.py` | `/reports/` and `/reports/coverage-risk/` |
| `prsb/scripts/coverage_risk.py` | Scoring: attendance + song/part risk |
| `prsb/band/templates/band/index.html` | Link to Reports |
| `prsb/band/templates/band/reports.html` | Reports hub |
| `prsb/band/templates/band/coverage_risk.html` | Expandable risk report |
| `prsb/band/tests.py` | Unit + view tests |

---

### Task 1: `Gig.is_small_group` field

**Files:**
- Modify: `prsb/band/models.py` (`Gig`)
- Create: `prsb/band/migrations/00XX_gig_is_small_group.py` (via makemigrations)
- Modify: `prsb/band/views.py` (`GigForm.Meta.fields`)
- Modify: `prsb/band/admin.py` (`GigAdmin`)
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: existing `Gig` model
- Produces: `Gig.is_small_group: bool` default `False`; included in `GigForm` and admin

- [ ] **Step 1: Write the failing test**

Add to `prsb/band/tests.py`:

```python
class GigIsSmallGroupTestCase(TestCase):
    def test_default_is_full_band(self):
        gig = Gig.objects.create(
            name="Full Band Gig",
            start_datetime=timezone.now() - timedelta(days=1),
            end_datetime=timezone.now() - timedelta(days=1) + timedelta(hours=2),
        )
        self.assertFalse(gig.is_small_group)

    def test_can_mark_small_group(self):
        gig = Gig.objects.create(
            name="Small Gig",
            start_datetime=timezone.now() - timedelta(days=1),
            end_datetime=timezone.now() - timedelta(days=1) + timedelta(hours=2),
            is_small_group=True,
        )
        self.assertTrue(Gig.objects.get(pk=gig.pk).is_small_group)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.GigIsSmallGroupTestCase -v 2
```

Expected: FAIL (field does not exist / TypeError on create kwarg)

- [ ] **Step 3: Add model field and migrate**

In `prsb/band/models.py`, on `Gig` after `notes`:

```python
is_small_group = models.BooleanField(
    default=False,
    help_text="If checked, this gig is excluded from coverage-risk attendance stats.",
)
```

Run:

```bash
cd prsb && poetry run python manage.py makemigrations band --name gig_is_small_group
cd prsb && poetry run python manage.py migrate
```

- [ ] **Step 4: Expose on form and admin**

In `GigForm.Meta.fields`, add `'is_small_group'` after `'notes'`.

In `GigAdmin`:

```python
class GigAdmin(admin.ModelAdmin):
    list_display = ['name', gig_is_upcoming, gig_date, 'is_small_group']
    list_filter = ['start_datetime', 'is_small_group']
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.GigIsSmallGroupTestCase -v 2
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add prsb/band/models.py prsb/band/migrations/ prsb/band/views.py prsb/band/admin.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add Gig.is_small_group for coverage-risk attendance filtering.

EOF
)"
```

---

### Task 2: Coverage risk scoring module (TDD)

**Files:**
- Create: `prsb/scripts/coverage_risk.py`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `Song`, `SongPart`, `PartAssignment`, `PerformanceReadiness`, `BandMember`, `Gig`, `GigAttendance`, `timezone.now`
- Produces:
  - `DEFAULT_LOOKBACK = 10`
  - `READY_WEIGHT = 1.0`, `BACKUP_WEIGHT = 0.5`, `RISK_EPSILON = 0.01`
  - `get_coverage_risk(lookback: int = DEFAULT_LOOKBACK) -> CoverageRiskReport`
  - Dataclasses (or equivalent named structures):

```python
@dataclass(frozen=True)
class CovererRow:
    member: BandMember
    instrument: Instrument
    readiness: str
    attendance_rate: float
    contribution: float  # readiness_weight * attendance_rate for this row

@dataclass(frozen=True)
class PartRiskRow:
    song_part: SongPart
    effective_coverage: float
    risk: float
    coverers: list[CovererRow]  # all ready/backup assignments; not_ready omitted

@dataclass(frozen=True)
class SongRiskRow:
    song: Song
    risk: float
    worst_part_name: str
    worst_part_effective_coverage: float
    parts: list[PartRiskRow]  # sorted risk desc
    unassigned_members: list[BandMember]  # active, no assignments on song

@dataclass(frozen=True)
class CoverageRiskReport:
    lookback_requested: int
    lookback_gigs_used: int  # D
    songs: list[SongRiskRow]  # sorted risk desc
```

Scoring rules (must match spec):

1. Lookback gigs: `Gig.objects.filter(is_small_group=False, start_datetime__lte=now).order_by('-start_datetime')[:lookback]`
2. `D =` number of gigs returned; if `D == 0`, every member rate = `1.0`
3. Else rate = count of `GigAttendance` with `status=AVAILABLE` for that member among those gigs, divided by `D`
4. For each rotation song part: among ready/backup assignments, group by member; best weight wins for `effective_coverage`; still emit a `CovererRow` per assignment for display
5. `part_risk = 1 / (effective_coverage + RISK_EPSILON)`
6. Song risk = max part risk (songs with no parts: treat as risk `1 / RISK_EPSILON` or skip — prefer include with empty parts list and risk `0` only if that never happens; if no parts, `risk = 1 / RISK_EPSILON` and empty worst-part strings)
7. Unassigned: active `BandMember`s with no `PartAssignment` whose `song_part__song` is this song

- [ ] **Step 1: Write the failing tests**

Add `CoverageRiskScoringTestCase` to `prsb/band/tests.py` (keep helpers local to the class):

```python
from scripts.coverage_risk import get_coverage_risk, DEFAULT_LOOKBACK


class CoverageRiskScoringTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.lead = Instrument.objects.create(name="Lead", order=0)
        cls.bass = Instrument.objects.create(name="Bass", order=1)

        cls.alice_user = User.objects.create_user(username="cr_alice", first_name="Alice", last_name="A")
        cls.bob_user = User.objects.create_user(username="cr_bob", first_name="Bob", last_name="B")
        cls.cara_user = User.objects.create_user(username="cr_cara", first_name="Cara", last_name="C")
        cls.alice = BandMember.objects.get(user=cls.alice_user)
        cls.bob = BandMember.objects.get(user=cls.bob_user)
        cls.cara = BandMember.objects.get(user=cls.cara_user)

        cls.song = Song.objects.create(title="Yellow Bird", in_gig_rotation=True)
        cls.other = Song.objects.create(title="Not In Rotation", in_gig_rotation=False)
        cls.part_lead = SongPart.objects.create(song=cls.song, name="Lead")
        cls.part_bass = SongPart.objects.create(song=cls.song, name="Bass")
        SongPart.objects.create(song=cls.other, name="Lead")

    def _past_gig(self, days_ago, *, small=False, name=None):
        start = self.now - timedelta(days=days_ago)
        return Gig.objects.create(
            name=name or f"Gig-{days_ago}",
            start_datetime=start,
            end_datetime=start + timedelta(hours=2),
            is_small_group=small,
        )

    def test_zero_gigs_attendance_rate_is_one(self):
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        self.assertEqual(report.lookback_gigs_used, 0)
        song = report.songs[0]
        lead = next(p for p in song.parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 1.0)
        self.assertAlmostEqual(lead.coverers[0].attendance_rate, 1.0)

    def test_small_group_and_future_gigs_excluded(self):
        full = self._past_gig(2, name="Full")
        self._past_gig(1, small=True, name="Small")
        future = Gig.objects.create(
            name="Future",
            start_datetime=self.now + timedelta(days=3),
            end_datetime=self.now + timedelta(days=3, hours=2),
        )
        GigAttendance.objects.create(gig=full, member=self.alice, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=future, member=self.alice, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        self.assertEqual(report.lookback_gigs_used, 1)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.coverers[0].attendance_rate, 1.0)

    def test_attendance_denominator_is_lookback_count(self):
        g1 = self._past_gig(3)
        g2 = self._past_gig(2)
        GigAttendance.objects.create(gig=g1, member=self.alice, status=GigAttendance.AVAILABLE)
        # g2: no response for alice
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        self.assertEqual(report.lookback_gigs_used, 2)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 0.5)
        self.assertAlmostEqual(lead.coverers[0].attendance_rate, 0.5)

    def test_backup_weight_and_not_ready_ignored(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=gig, member=self.bob, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.BACKUP,
        )
        PartAssignment.objects.create(
            member=self.bob, song_part=self.part_lead, instrument=self.bass,
            performance_readiness=PerformanceReadiness.NOT_READY,
        )
        report = get_coverage_risk(lookback=10)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 0.5)  # backup * 1.0
        self.assertEqual(len(lead.coverers), 1)

    def test_one_contribution_per_member_best_readiness(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.BACKUP,
        )
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.bass,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 1.0)
        self.assertEqual(len(lead.coverers), 2)

    def test_member_contributes_to_multiple_parts(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_bass, instrument=self.bass,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        song = next(s for s in report.songs if s.song.id == self.song.id)
        by_part = {p.song_part.name: p.effective_coverage for p in song.parts}
        self.assertAlmostEqual(by_part["Lead"], 1.0)
        self.assertAlmostEqual(by_part["Bass"], 1.0)

    def test_song_risk_is_worst_part_rotation_only(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        # Bass uncovered
        report = get_coverage_risk(lookback=10)
        titles = [s.song.title for s in report.songs]
        self.assertIn("Yellow Bird", titles)
        self.assertNotIn("Not In Rotation", titles)
        song = next(s for s in report.songs if s.song.title == "Yellow Bird")
        self.assertEqual(song.worst_part_name, "Bass")
        self.assertAlmostEqual(song.risk, 1 / 0.01)

    def test_unassigned_members_on_song(self):
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        song = next(s for s in report.songs if s.song.id == self.song.id)
        names = {m.user.username for m in song.unassigned_members}
        self.assertNotIn("cr_alice", names)
        self.assertIn("cr_bob", names)
        self.assertIn("cr_cara", names)
```


- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.CoverageRiskScoringTestCase -v 2
```

Expected: FAIL (cannot import `scripts.coverage_risk`)

- [ ] **Step 3: Implement `prsb/scripts/coverage_risk.py`**

Create the module with the dataclasses and `get_coverage_risk` implementing the rules in **Interfaces** above. Prefer clear named helpers (`_lookback_gigs`, `_attendance_rates`, `_score_part`) inside the same file — do not put scoring in `views.py`.

Import path must work the same way as `scripts.gig_part_assignment` (tests already import from `scripts.*`).

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.CoverageRiskScoringTestCase -v 2
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prsb/scripts/coverage_risk.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add coverage risk scoring with attendance-weighted effective coverage.

EOF
)"
```

---

### Task 3: Reports hub and Coverage Risk pages

**Files:**
- Modify: `prsb/band/urls.py`
- Modify: `prsb/band/views.py`
- Modify: `prsb/band/templates/band/index.html`
- Create: `prsb/band/templates/band/reports.html`
- Create: `prsb/band/templates/band/coverage_risk.html`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `get_coverage_risk(lookback: int) -> CoverageRiskReport`
- Produces:
  - URL names: `band:reports`, `band:coverage_risk`
  - `CoverageRiskView` reads `n` from query string; invalid/missing/non-positive → `DEFAULT_LOOKBACK`
  - Context: `report` (`CoverageRiskReport`), `lookback` (int used)

- [ ] **Step 1: Write the failing view tests**

```python
class CoverageRiskViewsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="viewer", password="pass")
        Song.objects.create(title="Rotation Song", in_gig_rotation=True)

    def test_reports_hub_ok(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:reports"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Coverage Risk")

    def test_coverage_risk_ok_default_n(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:coverage_risk"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["lookback"], 10)

    def test_coverage_risk_n_query_param(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:coverage_risk"), {"n": "5"})
        self.assertEqual(resp.context["lookback"], 5)

    def test_coverage_risk_invalid_n_defaults(self):
        self.client.login(username="viewer", password="pass")
        for bad in ["0", "-3", "abc"]:
            resp = self.client.get(reverse("band:coverage_risk"), {"n": bad})
            self.assertEqual(resp.context["lookback"], 10, msg=bad)
```


- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.CoverageRiskViewsTestCase -v 2
```

Expected: FAIL (URL `band:reports` not found)

- [ ] **Step 3: Add views and URLs**

In `views.py`:

```python
from scripts.coverage_risk import DEFAULT_LOOKBACK, get_coverage_risk


def _parse_lookback(request) -> int:
    raw = request.GET.get("n")
    if raw is None:
        return DEFAULT_LOOKBACK
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK
    if n < 1:
        return DEFAULT_LOOKBACK
    return n


class ReportsView(generic.TemplateView):
    template_name = "band/reports.html"


class CoverageRiskView(generic.TemplateView):
    template_name = "band/coverage_risk.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lookback = _parse_lookback(self.request)
        context["lookback"] = lookback
        context["report"] = get_coverage_risk(lookback=lookback)
        return context
```

In `urls.py`, before `health/`:

```python
path("reports/", views.ReportsView.as_view(), name="reports"),
path("reports/coverage-risk/", views.CoverageRiskView.as_view(), name="coverage_risk"),
```

- [ ] **Step 4: Add templates and Home link**

`index.html` — add after Instruments (or near other top links):

```html
<a href="{% url 'band:reports' %}">Reports</a>
<br />
```

`reports.html`:

```django
{% extends "band/base.html" %}
{% block title %}Reports | PRSB{% endblock %}
{% block nav %}
    <a href="{% url 'band:index' %}">Home</a>
    > Reports
{% endblock %}
{% block content %}
    <h2>Reports</h2>
    <ul>
        <li><a href="{% url 'band:coverage_risk' %}">Coverage Risk</a> — which rotation songs/parts are most fragile given attendance-weighted coverage</li>
    </ul>
{% endblock %}
```

`coverage_risk.html` — expandable list using `<details>` / `<summary>`:

```django
{% extends "band/base.html" %}
{% block title %}Coverage Risk | PRSB{% endblock %}
{% block nav %}
    <a href="{% url 'band:index' %}">Home</a>
    > <a href="{% url 'band:reports' %}">Reports</a>
    > Coverage Risk
{% endblock %}
{% block content %}
    <h2>Coverage Risk</h2>
    <p>
        Rotation songs ranked by worst-part risk.
        Effective coverage = sum of (readiness weight × attendance rate) per coverer;
        ready=1.0, backup=0.5. Attendance from the last {{ lookback }} past full-band gig{{ lookback|pluralize }}
        ({{ report.lookback_gigs_used }} used).
    </p>
    <form method="get">
        <label>
            Lookback (full-band gigs):
            <input type="number" name="n" min="1" value="{{ lookback }}">
        </label>
        <button type="submit">Update</button>
    </form>

    {% for song in report.songs %}
        <details {% if forloop.first %}open{% endif %}>
            <summary>
                <strong>{{ song.song.title }}</strong>
                — risk {{ song.risk|floatformat:2 }}
                · worst: {{ song.worst_part_name }}
                ({{ song.worst_part_effective_coverage|floatformat:2 }} eff.)
            </summary>
            {% for part in song.parts %}
                <h4>{{ part.song_part.name }} — effective {{ part.effective_coverage|floatformat:2 }} · risk {{ part.risk|floatformat:2 }}</h4>
                <table>
                    <thead>
                        <tr>
                            <th>Coverer</th>
                            <th>Instrument</th>
                            <th>Readiness</th>
                            <th>Attend rate</th>
                            <th>Contrib</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for c in part.coverers %}
                            <tr {% if c.readiness == "backup" %}class="warning"{% endif %}>
                                <td>{{ c.member }}</td>
                                <td>{{ c.instrument }}</td>
                                <td>{{ c.readiness }}</td>
                                <td>{{ c.attendance_rate|floatformat:2 }}</td>
                                <td>{{ c.contribution|floatformat:2 }}</td>
                            </tr>
                        {% empty %}
                            <tr class="error"><td colspan="5">No ready/backup coverers</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            {% endfor %}
            <p>
                <strong>No assignment on this song:</strong>
                {% for m in song.unassigned_members %}{{ m }}{% if not forloop.last %}, {% endif %}{% empty %}(none){% endfor %}
            </p>
        </details>
    {% empty %}
        <p>No songs in gig rotation.</p>
    {% endfor %}
{% endblock %}
```

`attendance_rate` is stored as 0–1 (e.g. `0.80`); the Attend rate column shows that fraction with `floatformat:2`.


- [ ] **Step 5: Run view tests**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.CoverageRiskViewsTestCase -v 2
```

Expected: PASS

- [ ] **Step 6: Run full band test suite**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests -v 2
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add prsb/band/urls.py prsb/band/views.py prsb/band/templates/band/index.html prsb/band/templates/band/reports.html prsb/band/templates/band/coverage_risk.html prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add Reports hub and Coverage Risk page.

EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|---|---|
| `Gig.is_small_group` default false | 1 |
| Editable on gig create/update + admin | 1 |
| Past full-band lookback, N default 10 | 2, 3 |
| Attendance = available / D; D = found count | 2 |
| Zero gigs → rate 1.0 | 2 |
| ready/backup weights; not_ready ignored | 2 |
| One contribution per member per part | 2 |
| Multi-part same member still counts each part | 2 |
| Song risk = worst part; rotation only | 2 |
| Coverers + unassigned-on-song | 2, 3 |
| Reports hub from Home | 3 |
| Expandable song list UI | 3 |
| `n` query param defaults | 3 |
| Unit + view tests | 1–3 |
