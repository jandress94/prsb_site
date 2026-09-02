# Dietary Restrictions Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a login-required report of active members’ non-empty dietary restrictions, optionally filtered by gig attendance (Available / Maybe available / No status yet).

**Architecture:** Pure query helpers in `band/dietary_restrictions.py` parse GET params and return a `BandMember` queryset. A thin `LoginRequiredMixin` template view at `/reports/dietary-restrictions/` renders a GET form (searchable gig radio list + status checkboxes) and a two-column table. No new JS libraries; no site-wide login middleware.

**Tech Stack:** Django 5, `LoginRequiredMixin`, `django.db.models.functions.Trim`, Django `TestCase`, existing `BandMember` / `Gig` / `GigAttendance` models.

## Global Constraints

- Authenticate this page only; do not enable `prsb.middleware.LoginRequiredMiddleware`
- Active members: `user__is_active=True`
- Exclude blank and whitespace-only `dietary_restrictions` via `Trim`
- Gig status options: `available`, `maybe_available`, `no_status` (no `unavailable` checkbox; unavailable members never match)
- `status` key absent + valid gig → default `["available"]`; `status` present with no allowed values → empty member list
- No gig (missing / invalid id) → ignore `status`; show all qualifying members
- Gig picker: vanilla search box + radios; upcoming listed when search is empty; typing matches all gigs by name or `Y-m-d` date
- Table columns: member name and dietary restriction text
- No Select2 / Tom Select / new CDN or npm widgets

---

## File Structure

| File | Responsibility |
|------|----------------|
| `prsb/band/dietary_restrictions.py` | Parse gig/status; queryset; gig lists for the picker |
| `prsb/band/views.py` | `DietaryRestrictionsView` |
| `prsb/band/urls.py` | `/reports/dietary-restrictions/`; name the login route `login` |
| `prsb/prsb/settings.py` | `LOGIN_URL = "band:login"` so the mixin reverses the existing login page |
| `prsb/band/templates/band/dietary_restrictions.html` | Form, table, inline filter script |
| `prsb/band/templates/band/reports.html` | Hub link |
| `prsb/band/tests.py` | Query helper tests + view tests |

---

### Task 1: Query helpers

**Files:**
- Create: `prsb/band/dietary_restrictions.py`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `BandMember`, `Gig`, `GigAttendance`, `Trim`, `timezone`
- Produces:
  - `STATUS_NO_STATUS = "no_status"`
  - `ALLOWED_STATUSES = frozenset({GigAttendance.AVAILABLE, GigAttendance.MAYBE_AVAILABLE, STATUS_NO_STATUS})`
  - `resolve_gig(raw: str | None) -> Gig | None`
  - `resolve_statuses(*, gig: Gig | None, status_key_present: bool, raw_values: list[str]) -> list[str] | None` — `None` means do not filter by attendance
  - `dietary_restriction_members(*, gig: Gig | None, statuses: list[str] | None)` → `QuerySet[BandMember]`
  - `upcoming_gigs()` → `QuerySet[Gig]` (`end_datetime >= now`, `start_datetime` ascending)
  - `gig_picker_rows(gigs) -> list[dict]` with keys `id`, `name`, `date` (`date` is local `Y-m-d`)

- [ ] **Step 1: Write the failing tests**

Append to `prsb/band/tests.py`:

```python
from band.dietary_restrictions import (
    STATUS_NO_STATUS,
    dietary_restriction_members,
    gig_picker_rows,
    resolve_gig,
    resolve_statuses,
    upcoming_gigs,
)


class DietaryRestrictionQueryTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        def member(username, first, last, diet, active=True):
            user = User.objects.create_user(
                username=username, first_name=first, last_name=last, is_active=active,
            )
            bm = user.bandmember
            bm.dietary_restrictions = diet
            bm.save()
            return bm

        cls.alice = member("dr_alice", "Alice", "Diet", "Vegetarian")
        cls.bob = member("dr_bob", "Bob", "Diet", "Peanut allergy")
        cls.cara = member("dr_cara", "Cara", "Diet", "   ")
        cls.dana = member("dr_dana", "Dana", "Diet", "")
        cls.erin = member("dr_erin", "Erin", "Diet", "Gluten free", active=False)
        cls.finn = member("dr_finn", "Finn", "Diet", "No shellfish")

        now = timezone.now()
        cls.gig = Gig.objects.create(
            name="Harvest Festival",
            start_datetime=now + timedelta(days=10),
            end_datetime=now + timedelta(days=10, hours=2),
        )
        cls.past_gig = Gig.objects.create(
            name="Oak Tree Picnic",
            start_datetime=now - timedelta(days=20),
            end_datetime=now - timedelta(days=20) + timedelta(hours=2),
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.alice, status=GigAttendance.AVAILABLE,
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.bob, status=GigAttendance.MAYBE_AVAILABLE,
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.finn, status=GigAttendance.UNAVAILABLE,
        )

    def test_default_excludes_blank_whitespace_inactive(self):
        names = [m.user.first_name for m in dietary_restriction_members(gig=None, statuses=None)]
        self.assertEqual(names, ["Alice", "Bob", "Finn"])

    def test_resolve_gig_invalid(self):
        self.assertIsNone(resolve_gig(None))
        self.assertIsNone(resolve_gig(""))
        self.assertIsNone(resolve_gig("abc"))
        self.assertIsNone(resolve_gig("99999"))
        self.assertEqual(resolve_gig(str(self.gig.pk)), self.gig)

    def test_resolve_statuses_ignored_without_gig(self):
        self.assertIsNone(resolve_statuses(
            gig=None, status_key_present=True, raw_values=["no_status"],
        ))

    def test_resolve_statuses_default_available(self):
        self.assertEqual(
            resolve_statuses(gig=self.gig, status_key_present=False, raw_values=[]),
            [GigAttendance.AVAILABLE],
        )

    def test_resolve_statuses_explicit_empty(self):
        self.assertEqual(
            resolve_statuses(gig=self.gig, status_key_present=True, raw_values=[""]),
            [],
        )
        self.assertEqual(
            resolve_statuses(
                gig=self.gig, status_key_present=True, raw_values=["unavailable"],
            ),
            [],
        )

    def test_resolve_statuses_union_preserves_allowed_order(self):
        self.assertEqual(
            resolve_statuses(
                gig=self.gig,
                status_key_present=True,
                raw_values=["no_status", "available", "no_status", "bogus"],
            ),
            [STATUS_NO_STATUS, GigAttendance.AVAILABLE],
        )

    def test_gig_available_default(self):
        statuses = resolve_statuses(gig=self.gig, status_key_present=False, raw_values=[])
        names = [m.user.first_name for m in dietary_restriction_members(gig=self.gig, statuses=statuses)]
        self.assertEqual(names, ["Alice"])

    def test_gig_available_and_no_status(self):
        members = dietary_restriction_members(
            gig=self.gig, statuses=[GigAttendance.AVAILABLE, STATUS_NO_STATUS],
        )
        names = [m.user.first_name for m in members]
        self.assertEqual(names, ["Alice"])

    def test_gig_maybe_and_no_status_includes_cara_gap(self):
        # Cara has whitespace-only diet so she is never in the base set.
        # Dana has empty diet. No extra member has a diet and no attendance row
        # except we need one: create in this test.
        user = User.objects.create_user(username="dr_gabe", first_name="Gabe", last_name="Diet")
        gabe = user.bandmember
        gabe.dietary_restrictions = "Kosher"
        gabe.save()
        members = dietary_restriction_members(
            gig=self.gig, statuses=[GigAttendance.MAYBE_AVAILABLE, STATUS_NO_STATUS],
        )
        names = [m.user.first_name for m in members]
        self.assertEqual(names, ["Bob", "Gabe"])

    def test_unavailable_never_included(self):
        members = dietary_restriction_members(
            gig=self.gig,
            statuses=[GigAttendance.AVAILABLE, GigAttendance.MAYBE_AVAILABLE, STATUS_NO_STATUS],
        )
        names = [m.user.first_name for m in members]
        self.assertNotIn("Finn", names)

    def test_empty_statuses_yields_no_members(self):
        self.assertEqual(list(dietary_restriction_members(gig=self.gig, statuses=[])), [])

    def test_upcoming_gigs_and_picker_rows(self):
        upcoming = list(upcoming_gigs())
        self.assertEqual(upcoming, [self.gig])
        rows = gig_picker_rows(Gig.objects.all())
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[self.gig.pk]["name"], "Harvest Festival")
        self.assertRegex(by_id[self.gig.pk]["date"], r"^\d{4}-\d{2}-\d{2}$")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.DietaryRestrictionQueryTestCase
```

Expected: FAIL with `ModuleNotFoundError: No module named 'band.dietary_restrictions'` (or import error).

- [ ] **Step 3: Write the helpers**

Create `prsb/band/dietary_restrictions.py`:

```python
from django.db.models import Exists, OuterRef, QuerySet
from django.db.models.functions import Trim
from django.utils import timezone

from .models import BandMember, Gig, GigAttendance

STATUS_NO_STATUS = "no_status"
ALLOWED_STATUSES = frozenset({
    GigAttendance.AVAILABLE,
    GigAttendance.MAYBE_AVAILABLE,
    STATUS_NO_STATUS,
})


def resolve_gig(raw: str | None) -> Gig | None:
    if raw is None or raw == "":
        return None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None
    return Gig.objects.filter(pk=pk).first()


def resolve_statuses(
    *,
    gig: Gig | None,
    status_key_present: bool,
    raw_values: list[str],
) -> list[str] | None:
    if gig is None:
        return None
    if not status_key_present:
        return [GigAttendance.AVAILABLE]
    seen: list[str] = []
    for value in raw_values:
        if value in ALLOWED_STATUSES and value not in seen:
            seen.append(value)
    return seen


def _base_members() -> QuerySet:
    return (
        BandMember.objects.annotate(_diet=Trim("dietary_restrictions"))
        .filter(user__is_active=True)
        .exclude(_diet="")
        .order_by("user__first_name", "user__last_name")
    )


def dietary_restriction_members(
    *,
    gig: Gig | None,
    statuses: list[str] | None,
) -> QuerySet:
    qs = _base_members()
    if gig is None or statuses is None:
        return qs
    if not statuses:
        return qs.none()

    attendance = GigAttendance.objects.filter(gig=gig, member=OuterRef("pk"))
    clauses = []
    if GigAttendance.AVAILABLE in statuses:
        clauses.append(Exists(attendance.filter(status=GigAttendance.AVAILABLE)))
    if GigAttendance.MAYBE_AVAILABLE in statuses:
        clauses.append(Exists(attendance.filter(status=GigAttendance.MAYBE_AVAILABLE)))
    if STATUS_NO_STATUS in statuses:
        clauses.append(~Exists(attendance))

    combined = clauses[0]
    for clause in clauses[1:]:
        combined |= clause
    return qs.filter(combined)


def upcoming_gigs() -> QuerySet:
    return Gig.objects.filter(end_datetime__gte=timezone.now()).order_by("start_datetime")


def gig_picker_rows(gigs) -> list[dict]:
    rows = []
    for gig in gigs:
        local = timezone.localtime(gig.start_datetime)
        rows.append({
            "id": gig.pk,
            "name": gig.name,
            "date": local.strftime("%Y-%m-%d"),
        })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.DietaryRestrictionQueryTestCase
```

Expected: OK (all tests pass). If `test_gig_maybe_and_no_status_includes_cara_gap` fails because Gabe sorts before Bob, check first-name order (`Bob`, `Gabe` is correct).

- [ ] **Step 5: Commit**

```bash
git add prsb/band/dietary_restrictions.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add dietary restriction queryset helpers.

EOF
)"
```

---

### Task 2: Login-required view, URL, and report page

**Files:**
- Modify: `prsb/band/views.py` (imports + view after `CoverageRiskView`)
- Modify: `prsb/band/urls.py` (login `name="login"`; new report path)
- Modify: `prsb/prsb/settings.py` (`LOGIN_URL`)
- Create: `prsb/band/templates/band/dietary_restrictions.html`
- Modify: `prsb/band/templates/band/reports.html`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: helpers from Task 1; `LoginRequiredMixin`
- Produces: named URL `band:dietary_restrictions`; context keys `members`, `gig`, `statuses`, `upcoming_gigs`, `all_gigs` (list of `{id, name, date}`), `status_fieldset_enabled` (bool)

- [ ] **Step 1: Write the failing view tests**

Append to `prsb/band/tests.py` (reuse the same member/gig setup pattern, or subclass / call the query test data if you prefer a dedicated `setUpTestData`):

```python
class DietaryRestrictionsViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = User.objects.create_user(username="dr_viewer", password="pass")
        alice_user = User.objects.create_user(username="dr_view_alice", first_name="Alice", last_name="View")
        bob_user = User.objects.create_user(username="dr_view_bob", first_name="Bob", last_name="View")
        finn_user = User.objects.create_user(username="dr_view_finn", first_name="Finn", last_name="View")
        gabe_user = User.objects.create_user(username="dr_view_gabe", first_name="Gabe", last_name="View")
        cls.alice = alice_user.bandmember
        cls.alice.dietary_restrictions = "Vegetarian, no mushrooms"
        cls.alice.save()
        cls.bob = bob_user.bandmember
        cls.bob.dietary_restrictions = "Peanut allergy"
        cls.bob.save()
        cls.finn = finn_user.bandmember
        cls.finn.dietary_restrictions = "No shellfish"
        cls.finn.save()
        cls.gabe = gabe_user.bandmember
        cls.gabe.dietary_restrictions = "Kosher"
        cls.gabe.save()
        now = timezone.now()
        cls.gig = Gig.objects.create(
            name="Harvest Festival",
            start_datetime=now + timedelta(days=5),
            end_datetime=now + timedelta(days=5, hours=2),
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.alice, status=GigAttendance.AVAILABLE,
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.bob, status=GigAttendance.MAYBE_AVAILABLE,
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.finn, status=GigAttendance.UNAVAILABLE,
        )

    def _get(self, **params):
        self.client.login(username="dr_viewer", password="pass")
        return self.client.get(reverse("band:dietary_restrictions"), params)

    def test_anonymous_redirects_to_login(self):
        resp = self.client.get(reverse("band:dietary_restrictions"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_signed_in_ok(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Vegetarian, no mushrooms")
        self.assertContains(resp, "Bob View")
        self.assertContains(resp, "Peanut allergy")
        self.assertContains(resp, "Gabe View")
        self.assertContains(resp, "Kosher")

    def test_gig_without_status_param_defaults_available(self):
        resp = self._get(gig=str(self.gig.pk))
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Vegetarian, no mushrooms")
        self.assertNotContains(resp, "Bob View")
        self.assertNotContains(resp, "Gabe View")
        self.assertNotContains(resp, "Finn View")

    def test_gig_available_and_no_status(self):
        resp = self._get(gig=str(self.gig.pk), status=["available", "no_status"])
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Gabe View")
        self.assertNotContains(resp, "Bob View")

    def test_explicit_empty_status_is_empty_table(self):
        resp = self._get(gig=str(self.gig.pk), status=[""])
        self.assertNotContains(resp, "Alice View")
        self.assertContains(resp, "No members match")

    def test_junk_status_only_is_empty_table(self):
        resp = self._get(gig=str(self.gig.pk), status=["unavailable"])
        self.assertNotContains(resp, "Alice View")
        self.assertContains(resp, "No members match")

    def test_invalid_gig_is_unfiltered(self):
        resp = self._get(gig="abc", status=["no_status"])
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Bob View")
        self.assertContains(resp, "Gabe View")

    def test_status_without_gig_is_ignored(self):
        resp = self._get(status=["no_status"])
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Bob View")

    def test_hub_links_to_report(self):
        self.client.login(username="dr_viewer", password="pass")
        resp = self.client.get(reverse("band:reports"))
        self.assertContains(resp, reverse("band:dietary_restrictions"))
        self.assertContains(resp, "Dietary Restrictions")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.DietaryRestrictionsViewTestCase
```

Expected: FAIL (`NoReverseMatch` for `band:dietary_restrictions` or 404).

- [ ] **Step 3: Wire URL, login name, LOGIN_URL, view, template, hub**

In `prsb/band/urls.py`, name login and add the report path (keep other paths unchanged):

```python
path("accounts/login/", LoginView.as_view(template_name="band/registration/login.html"), name="login"),
```

```python
path("reports/dietary-restrictions/", views.DietaryRestrictionsView.as_view(), name="dietary_restrictions"),
```

Place the new path immediately after `path("reports/coverage-risk/", ...)`.

In `prsb/prsb/settings.py`, next to `LOGIN_REDIRECT_URL`:

```python
LOGIN_URL = "band:login"
```

In `prsb/band/views.py`, add imports:

```python
from django.contrib.auth.mixins import LoginRequiredMixin

from .dietary_restrictions import (
    dietary_restriction_members,
    gig_picker_rows,
    resolve_gig,
    resolve_statuses,
    upcoming_gigs,
)
from .models import ... Gig  # Gig is already imported
```

`Gig` is already in the models import. Add this view after `CoverageRiskView`:

```python
class DietaryRestrictionsView(LoginRequiredMixin, generic.TemplateView):
    template_name = "band/dietary_restrictions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gig = resolve_gig(self.request.GET.get("gig"))
        statuses = resolve_statuses(
            gig=gig,
            status_key_present="status" in self.request.GET,
            raw_values=self.request.GET.getlist("status"),
        )
        context["gig"] = gig
        context["statuses"] = statuses or []
        context["status_fieldset_enabled"] = gig is not None
        context["members"] = dietary_restriction_members(gig=gig, statuses=statuses)
        upcoming = list(upcoming_gigs())
        context["upcoming_gigs"] = upcoming
        all_gigs = list(Gig.objects.order_by("start_datetime", "pk"))
        context["all_gigs"] = gig_picker_rows(all_gigs)
        return context
```

Create `prsb/band/templates/band/dietary_restrictions.html`:

```html
{% extends "band/base.html" %}
{% load tz %}
{% block title %}Dietary Restrictions | PRSB{% endblock %}
{% block nav %}
    <a href="{% url 'band:index' %}">Home</a>
    > <a href="{% url 'band:reports' %}">Reports</a>
    > Dietary Restrictions
{% endblock %}
{% block content %}
    <h2>Dietary Restrictions</h2>
    <p>
        {% if gig %}
            {{ gig.name }}
            ·
            {% if statuses %}
                {% for s in statuses %}{{ s }}{% if not forloop.last %}, {% endif %}{% endfor %}
            {% else %}
                no statuses selected
            {% endif %}
        {% else %}
            All active members with a dietary restriction
        {% endif %}
    </p>
    <form method="get" id="dietary-filter-form">
        <label for="gig-search">Gig (optional)</label>
        <input type="search" id="gig-search" placeholder="Type to search all gigs by name or date…" autocomplete="off">
        <div id="gig-list" role="radiogroup" aria-label="Gig">
            <label class="gig-option" data-always-visible="1">
                <input type="radio" name="gig" value="" {% if not gig %}checked{% endif %}>
                All active members
            </label>
            {% for row in all_gigs %}
                <label class="gig-option"
                       data-gig-id="{{ row.id }}"
                       data-search="{{ row.name|lower }} {{ row.date }}"
                       {% if gig and gig.pk == row.id %}data-selected="1"{% endif %}>
                    <input type="radio" name="gig" value="{{ row.id }}" {% if gig and gig.pk == row.id %}checked{% endif %}>
                    {{ row.name }}
                    <span>{{ row.date }}</span>
                </label>
            {% endfor %}
        </div>
        <fieldset id="status-fieldset" {% if not status_fieldset_enabled %}disabled{% endif %}>
            <legend>Include attendance status</legend>
            <input type="hidden" name="status" value="">
            <label><input type="checkbox" name="status" value="available" {% if not gig or "available" in statuses %}checked{% endif %}> Available</label>
            <label><input type="checkbox" name="status" value="maybe_available" {% if "maybe_available" in statuses %}checked{% endif %}> Maybe available</label>
            <label><input type="checkbox" name="status" value="no_status" {% if "no_status" in statuses %}checked{% endif %}> No status yet</label>
        </fieldset>
        <button type="submit">Apply</button>
    </form>
    <table>
        <thead>
            <tr><th>Member</th><th>Dietary restriction</th></tr>
        </thead>
        <tbody>
            {% for member in members %}
                <tr>
                    <td>{{ member }}</td>
                    <td>{{ member.dietary_restrictions }}</td>
                </tr>
            {% empty %}
                <tr><td colspan="2">No members match</td></tr>
            {% endfor %}
        </tbody>
    </table>
    <script>
    (function () {
        const search = document.getElementById("gig-search");
        const options = Array.from(document.querySelectorAll("#gig-list .gig-option[data-gig-id]"));
        const upcomingIds = new Set({% for g in upcoming_gigs %}{{ g.pk }}{% if not forloop.last %},{% endif %}{% endfor %});
        const fieldset = document.getElementById("status-fieldset");
        const gigRadios = document.querySelectorAll('#gig-list input[name="gig"]');

        function selectedGigId() {
            const checked = document.querySelector('#gig-list input[name="gig"]:checked');
            return checked ? checked.value : "";
        }

        function syncStatusEnabled() {
            fieldset.disabled = !selectedGigId();
        }

        function filterList() {
            const q = (search.value || "").trim().toLowerCase();
            options.forEach(function (el) {
                const id = el.getAttribute("data-gig-id");
                const hay = el.getAttribute("data-search") || "";
                const selected = el.getAttribute("data-selected") === "1" || selectedGigId() === id;
                let show;
                if (selected) {
                    show = true;
                } else if (!q) {
                    show = upcomingIds.has(Number(id));
                } else {
                    show = hay.indexOf(q) !== -1;
                }
                el.hidden = !show;
            });
        }

        search.addEventListener("input", filterList);
        gigRadios.forEach(function (r) {
            r.addEventListener("change", function () {
                syncStatusEnabled();
                filterList();
            });
        });
        syncStatusEnabled();
        filterList();
    })();
    </script>
{% endblock %}
```

Default checkbox quirk: `{% if not gig or "available" in statuses %}checked{% endif %}` keeps Available checked on the unfiltered page (fieldset is disabled, so those boxes are not submitted). When a gig is selected with an empty `statuses` list, Available must **not** be checked. That is already true because `not gig` is false and `"available" in statuses` is false.

Replace the Coverage Risk-only list in `prsb/band/templates/band/reports.html` with:

```html
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
    <li><a href="{% url 'band:dietary_restrictions' %}">Dietary Restrictions</a> — members’ dietary restrictions (login required; personal information)</li>
    </ul>
{% endblock %}
```

- [ ] **Step 4: Run view tests**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.DietaryRestrictionsViewTestCase band.tests.DietaryRestrictionQueryTestCase
```

Expected: OK.

If `test_explicit_empty_status_is_empty_table` fails because Django’s test client omits empty `status` values, pass the query string explicitly:

```python
resp = self.client.get(reverse("band:dietary_restrictions") + f"?gig={self.gig.pk}&status=")
```

(after `self.client.login(...)`).

- [ ] **Step 5: Run the full band test suite**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests
```

Expected: OK. Existing coverage-risk / reports tests still pass. Logout still uses `next_page="login"`; if that reverse breaks because `login` is now namespaced, change it to `next_page="band:index"` or `next_page="band:login"` in the same commit (only if a test or a quick `reverse` check shows it is broken).

- [ ] **Step 6: Commit**

```bash
git add prsb/band/views.py prsb/band/urls.py prsb/prsb/settings.py \
  prsb/band/templates/band/dietary_restrictions.html \
  prsb/band/templates/band/reports.html \
  prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add login-required dietary restrictions report.

EOF
)"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|---|---|
| Login required; any signed-in member | 2 (`LoginRequiredMixin`, `LOGIN_URL`) |
| Do not enable site-wide middleware | 2 (settings change is `LOGIN_URL` only) |
| Active + Trim non-empty restrictions | 1 |
| Default all qualifying members | 1 + 2 |
| Gig optional; status ignored without gig | 1 + 2 |
| Statuses available / maybe / no_status; never unavailable | 1 |
| Default available when `status` omitted | 1 |
| Empty / junk `status` key → empty list | 1 + 2 |
| Table: name + restriction text | 2 |
| Gig picker search + upcoming radios | 2 |
| Hidden empty `status=` when fieldset enabled | 2 |
| Status fieldset disabled without gig | 2 |
| Reports hub link | 2 |
| Name login route if mixin needs it | 2 |

No Select2, no staff-only, no other reports.
