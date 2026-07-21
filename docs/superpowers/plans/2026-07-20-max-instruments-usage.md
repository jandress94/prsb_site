# Max Instruments Used Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a “Max Instruments Used” table on the gig part-assignments page showing peak concurrent use of each gig instrument across setlist songs (or proposed songs if no setlist).

**Architecture:** Pure helper `get_max_instrument_usage` aggregates per-song instrument counts from existing `GigPartAssignment` results. The detail view picks setlist vs recommendations (same scope rule as songs-per-person) and passes rows to the template. No changes to the MILP solver or `get_gig_part_assignments` return signature.

**Tech Stack:** Django, Django TestCase, existing `scripts.gig_part_assignment` module, Django templates.

## Global Constraints

- Scope songs: setlist GPA list if non-empty, else recommendation GPA list
- Include every `GigInstrument` for the gig, including Max Used = 0
- Columns: Instrument | Max Used | Available (`gig_quantity`)
- Do not change `get_gig_part_assignments` return signature
- No shortfall highlighting in v1

---

## File Structure

| File | Responsibility |
|------|----------------|
| `prsb/scripts/gig_part_assignment.py` | Add `get_max_instrument_usage` helper |
| `prsb/band/tests.py` | Unit tests for the helper |
| `prsb/band/views.py` | Wire helper into `GigPartAssignmentsDetailView` context |
| `prsb/band/templates/band/gig_part_assignments.html` | Render the new table |

---

### Task 1: `get_max_instrument_usage` helper (TDD)

**Files:**
- Modify: `prsb/scripts/gig_part_assignment.py`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `GigPartAssignment.part_assignments` (each item has `.instrument`); `GigInstrument.instrument`, `GigInstrument.gig_quantity`; `Instrument.order`
- Produces: `get_max_instrument_usage(gig_part_assignments, gig_instruments) -> list[tuple[Instrument, int, int]]` sorted by `instrument.order`, each tuple `(instrument, max_used, available)`

- [ ] **Step 1: Write the failing tests**

Add to `prsb/band/tests.py`:

```python
from types import SimpleNamespace
from scripts.gig_part_assignment import get_gig_part_assignments, get_max_instrument_usage


class MaxInstrumentUsageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lead = Instrument.objects.create(name="Lead", order=0)
        cls.tenor = Instrument.objects.create(name="Double Tenor", order=1)
        cls.congas = Instrument.objects.create(name="Congas", order=2)

        cls.gig = Gig.objects.create(
            name="Max Usage Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        cls.gi_lead = GigInstrument.objects.create(gig=cls.gig, instrument=cls.lead, gig_quantity=7)
        cls.gi_tenor = GigInstrument.objects.create(gig=cls.gig, instrument=cls.tenor, gig_quantity=1)
        cls.gi_congas = GigInstrument.objects.create(gig=cls.gig, instrument=cls.congas, gig_quantity=1)
        cls.gig_instruments = [cls.gi_lead, cls.gi_tenor, cls.gi_congas]

    def _gpa(self, *instruments):
        """Minimal stand-in: helper only reads part_assignments[].instrument."""
        return SimpleNamespace(
            part_assignments=[SimpleNamespace(instrument=inst) for inst in instruments]
        )

    def test_peak_across_songs(self):
        gpas = [
            self._gpa(self.lead, self.lead, self.lead),           # 3 Lead
            self._gpa(self.lead, self.lead, self.lead, self.lead, self.lead),  # 5 Lead
        ]
        result = get_max_instrument_usage(gpas, self.gig_instruments)
        by_name = {inst.name: (max_used, available) for inst, max_used, available in result}
        self.assertEqual(by_name["Lead"], (5, 7))

    def test_unused_instrument_shows_zero(self):
        gpas = [self._gpa(self.lead, self.tenor)]
        result = get_max_instrument_usage(gpas, self.gig_instruments)
        by_name = {inst.name: (max_used, available) for inst, max_used, available in result}
        self.assertEqual(by_name["Congas"], (0, 1))
        self.assertEqual([inst.name for inst, _, _ in result], ["Lead", "Double Tenor", "Congas"])

    def test_empty_assignments_all_zero(self):
        result = get_max_instrument_usage([], self.gig_instruments)
        self.assertEqual(
            [(inst.name, max_used, available) for inst, max_used, available in result],
            [("Lead", 0, 7), ("Double Tenor", 0, 1), ("Congas", 0, 1)],
        )
```

Also update the existing import line at the top of `tests.py` to include `get_max_instrument_usage` (shown above) instead of only `get_gig_part_assignments`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd prsb && python manage.py test band.tests.MaxInstrumentUsageTestCase -v 2
```

Expected: FAIL with `ImportError` or `AttributeError` that `get_max_instrument_usage` is not defined / cannot be imported.

- [ ] **Step 3: Implement the helper**

Add to `prsb/scripts/gig_part_assignment.py` (after the `GigPartAssignment` class is fine; before or after `get_gig_part_assignments`):

```python
def get_max_instrument_usage(
    gig_part_assignments: list[GigPartAssignment],
    gig_instruments: list[GigInstrument],
) -> list[tuple]:
    """Return (instrument, max_used, available) for each gig instrument, sorted by instrument order.

    max_used is the maximum number of players assigned to that instrument on any single song
    in gig_part_assignments. Instruments never used have max_used 0.
    """
    max_used_by_instrument: Counter = Counter()
    for gpa in gig_part_assignments:
        played = Counter(pa.instrument for pa in gpa.part_assignments)
        for instrument, count in played.items():
            if count > max_used_by_instrument[instrument]:
                max_used_by_instrument[instrument] = count

    rows = [
        (gi.instrument, max_used_by_instrument[gi.instrument], gi.gig_quantity)
        for gi in gig_instruments
    ]
    return sorted(rows, key=lambda row: row[0].order)
```

Note: `Counter` is already imported in this module. Return type can be annotated as `list[tuple]` or `list[Tuple[Instrument, int, int]]` — if using `Instrument` in the annotation, it is already imported from `band.models`. Prefer:

```python
) -> list[Tuple]:
```

or add `Instrument` is already imported — use `list[tuple]` to avoid typing friction, matching nearby style (`Tuple` is already imported).

Use this exact signature for consistency with the file’s existing `Tuple` import:

```python
def get_max_instrument_usage(
    gig_part_assignments: list[GigPartAssignment],
    gig_instruments: list[GigInstrument],
) -> list[Tuple]:
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd prsb && python manage.py test band.tests.MaxInstrumentUsageTestCase -v 2
```

Expected: OK — 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add prsb/scripts/gig_part_assignment.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add get_max_instrument_usage helper for peak instrument packing.

EOF
)"
```

---

### Task 2: Wire view and template

**Files:**
- Modify: `prsb/band/views.py` (import + `GigPartAssignmentsDetailView.get_context_data`)
- Modify: `prsb/band/templates/band/gig_part_assignments.html` (after songs-per-person table)

**Interfaces:**
- Consumes: `get_max_instrument_usage` from Task 1; `gig_part_assignments_setlist` / `gig_part_assignments_recs` from `get_gig_part_assignments`
- Produces: template context key `max_instrument_usage` — `list[tuple[Instrument, int, int]]`

- [ ] **Step 1: Update the import in views.py**

Change:

```python
from scripts.gig_part_assignment import get_gig_part_assignments, GigPartAssignment
```

to:

```python
from scripts.gig_part_assignment import get_gig_part_assignments, get_max_instrument_usage, GigPartAssignment
```

- [ ] **Step 2: Wire context in `GigPartAssignmentsDetailView.get_context_data`**

Replace the body of `get_context_data` so it becomes:

```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    gig_id = context['pk']

    context['gig'] = gig = Gig.objects.get(id=gig_id)

    context['part_assignment_overrides'] = GigPartAssignmentOverride.objects.filter(gig_instrument__gig=gig).order_by('song_part__song', 'song_part', 'member')

    context["gig_part_assignments_setlist"], context["gig_part_assignments_recs"], member_song_counts = get_gig_part_assignments(gig, context['part_assignment_overrides'])
    context['member_song_counts'] = sorted([(k, v) for k, v in member_song_counts.items()], key=lambda x: x[1], reverse=True)

    scoped_assignments = (
        context["gig_part_assignments_setlist"]
        if context["gig_part_assignments_setlist"]
        else context["gig_part_assignments_recs"]
    )
    gig_instruments = list(GigInstrument.objects.filter(gig=gig).select_related("instrument"))
    context["max_instrument_usage"] = get_max_instrument_usage(scoped_assignments, gig_instruments)

    return context
```

`GigInstrument` is already imported in `views.py` (confirm before editing; if not, add it to the `band.models` import).

- [ ] **Step 3: Add the table to the template**

In `prsb/band/templates/band/gig_part_assignments.html`, immediately after the songs-per-person `</table>` (before the `Options and Settings` heading), insert:

```html
    <h2>Max Instruments Used</h2>
    <table class="sortable">
        <thead>
            <tr>
                <th>Instrument</th>
                <th>Max Used</th>
                <th>Available</th>
            </tr>
        </thead>
        <tbody>
            {% for instrument, max_used, available in max_instrument_usage %}
                <tr>
                    <td>{{ instrument }}</td>
                    <td>{{ max_used }}</td>
                    <td>{{ available }}</td>
                </tr>
            {% endfor %}
        </tbody>
    </table>
```

- [ ] **Step 4: Sanity-check existing override tests still pass**

Run:

```bash
cd prsb && python manage.py test band.tests -v 2
```

Expected: all tests in `band.tests` OK (existing override tests + new MaxInstrumentUsage tests).

- [ ] **Step 5: Commit**

```bash
git add prsb/band/views.py prsb/band/templates/band/gig_part_assignments.html
git commit -m "$(cat <<'EOF'
Show max instruments used table on gig part assignments page.

EOF
)"
```

---

## Spec Coverage Checklist

| Spec requirement | Task |
|------------------|------|
| Helper computes max per instrument across songs | Task 1 |
| Include zeros / all gig instruments | Task 1 |
| Sorted by instrument order | Task 1 |
| Setlist if non-empty else recs | Task 2 |
| Table under songs-per-person: Instrument / Max Used / Available | Task 2 |
| No change to `get_gig_part_assignments` signature | Both (unchanged) |
| Unit tests for peak / unused / empty | Task 1 |
