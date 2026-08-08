# Past Gigs Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paginate past gigs on `/gigs/` at 10 per page with Prev / page numbers / Next, leaving upcoming gigs fully listed.

**Architecture:** Keep `GigListView` and paginate only the past-gigs queryset in `get_context_data` with Django’s `Paginator` and `?page=` via `get_page()`. Template renders controls from `page_obj` when there is more than one page.

**Tech Stack:** Django 5, `django.core.paginator.Paginator`, Django `TestCase` + test client, Django templates.

## Global Constraints

- Past gigs only: page size **10**, order `-start_datetime` (model default)
- URL: `/gigs/?page=N`; bad pages use `paginator.get_page` (200, valid page)
- Controls: Previous, all page numbers, Next; omit when `num_pages <= 1`
- Upcoming gigs: unchanged full list, `end_datetime >= now`, order `start_datetime` ascending
- No ellipsis window, no HTMX, no paginating upcoming

---

## File Structure

| File | Responsibility |
|------|----------------|
| `prsb/band/views.py` | Paginate past gigs in `GigListView.get_context_data` |
| `prsb/band/templates/band/gig_list.html` | Past-gigs pagination controls; fix `{% load tz %}qq` |
| `prsb/band/tests.py` | View tests for page size, page 2, invalid page, controls |

---

### Task 1: Paginate past gigs in `GigListView`

**Files:**
- Modify: `prsb/band/views.py` (`GigListView`, imports)
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `Gig` queryset; `request.GET["page"]`
- Produces: context keys `past_gigs` (page object list), `page_obj` (`Page`), `upcoming_gigs` (unchanged)

- [ ] **Step 1: Write the failing tests**

Append to `prsb/band/tests.py` (reuse existing imports: `TestCase`, `timezone`, `timedelta`, `reverse`, `Gig`; add nothing else unless missing):

```python
class GigListPastPaginationTestCase(TestCase):
    def _past_gig(self, days_ago, name=None):
        start = timezone.now() - timedelta(days=days_ago)
        return Gig.objects.create(
            name=name or f"Past-{days_ago}",
            start_datetime=start,
            end_datetime=start + timedelta(hours=2),
        )

    def _upcoming_gig(self, days_ahead, name=None):
        start = timezone.now() + timedelta(days=days_ahead)
        return Gig.objects.create(
            name=name or f"Upcoming-{days_ahead}",
            start_datetime=start,
            end_datetime=start + timedelta(hours=2),
        )

    def test_first_page_has_at_most_ten_past_gigs(self):
        for i in range(1, 16):
            self._past_gig(i, name=f"P{i}")
        self._upcoming_gig(1, name="U1")

        resp = self.client.get(reverse("band:gig_list"))
        self.assertEqual(resp.status_code, 200)
        past = list(resp.context["past_gigs"])
        self.assertEqual(len(past), 10)
        # Newest past first (days_ago=1 … 10)
        self.assertEqual([g.name for g in past], [f"P{i}" for i in range(1, 11)])
        upcoming = list(resp.context["upcoming_gigs"])
        self.assertEqual([g.name for g in upcoming], ["U1"])
        self.assertEqual(resp.context["page_obj"].number, 1)
        self.assertEqual(resp.context["page_obj"].paginator.num_pages, 2)

    def test_page_two_returns_remaining_past_gigs(self):
        for i in range(1, 16):
            self._past_gig(i, name=f"P{i}")

        resp = self.client.get(reverse("band:gig_list"), {"page": "2"})
        self.assertEqual(resp.status_code, 200)
        past = list(resp.context["past_gigs"])
        self.assertEqual([g.name for g in past], [f"P{i}" for i in range(11, 16)])
        self.assertEqual(resp.context["page_obj"].number, 2)

    def test_invalid_page_still_returns_200(self):
        for i in range(1, 12):
            self._past_gig(i, name=f"P{i}")

        for bad in ("999", "abc", "-1", "0"):
            with self.subTest(page=bad):
                resp = self.client.get(reverse("band:gig_list"), {"page": bad})
                self.assertEqual(resp.status_code, 200)
                self.assertIn("page_obj", resp.context)
                self.assertTrue(1 <= resp.context["page_obj"].number <= 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.GigListPastPaginationTestCase -v 2
```

Expected: FAIL — `past_gigs` length 15 (or missing `page_obj`), not paginated.

- [ ] **Step 3: Implement pagination in the view**

In `prsb/band/views.py`, add import:

```python
from django.core.paginator import Paginator
```

Replace `GigListView` with:

```python
class GigListView(generic.ListView):
    model = Gig

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['upcoming_gigs'] = Gig.objects.filter(
            end_datetime__gte=timezone.now()
        ).order_by('start_datetime')
        past_qs = Gig.objects.filter(end_datetime__lt=timezone.now())
        paginator = Paginator(past_qs, 10)
        page_obj = paginator.get_page(self.request.GET.get('page'))
        context['page_obj'] = page_obj
        context['past_gigs'] = page_obj.object_list
        return context
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.GigListPastPaginationTestCase -v 2
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add prsb/band/views.py prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Paginate past gigs on the /gigs list at 10 per page.

EOF
)"
```

---

### Task 2: Pagination controls in the template

**Files:**
- Modify: `prsb/band/templates/band/gig_list.html`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `page_obj` from Task 1 (`has_previous`, `previous_page_number`, `paginator.page_range`, `number`, `has_next`, `next_page_number`, `paginator.num_pages`)
- Produces: Prev / page-number / Next links under the past-gigs list

- [ ] **Step 1: Write the failing template tests**

Add methods to `GigListPastPaginationTestCase` in `prsb/band/tests.py`:

```python
    def test_pagination_controls_when_multiple_pages(self):
        for i in range(1, 12):
            self._past_gig(i, name=f"P{i}")

        resp = self.client.get(reverse("band:gig_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Previous", count=0)  # page 1: no Previous
        self.assertContains(resp, "Next")
        self.assertContains(resp, 'href="?page=2"')
        # Current page is not a link to itself as ?page=1 in a way that duplicates;
        # at minimum page 2 link exists and "1" appears as current.
        self.assertContains(resp, "<strong>1</strong>")

        resp2 = self.client.get(reverse("band:gig_list"), {"page": "2"})
        self.assertContains(resp2, "Previous")
        self.assertContains(resp2, 'href="?page=1"')
        self.assertContains(resp2, "Next", count=0)
        self.assertContains(resp2, "<strong>2</strong>")

    def test_no_pagination_controls_when_single_page(self):
        for i in range(1, 6):
            self._past_gig(i, name=f"P{i}")

        resp = self.client.get(reverse("band:gig_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Previous")
        self.assertNotContains(resp, "Next")
        self.assertNotContains(resp, 'href="?page=')
```

- [ ] **Step 2: Run new tests to verify they fail**

Run:

```bash
cd prsb && poetry run python manage.py test \
  band.tests.GigListPastPaginationTestCase.test_pagination_controls_when_multiple_pages \
  band.tests.GigListPastPaginationTestCase.test_no_pagination_controls_when_single_page -v 2
```

Expected: FAIL — template has no Prev/Next / page links / `<strong>` current page.

- [ ] **Step 3: Update the template**

Replace `prsb/band/templates/band/gig_list.html` entirely with:

```django
{% extends "band/base.html" %}
{% load tz %}

{% block title %}Gigs | PRSB{% endblock %}

{% block nav %}
    <a href={% url 'band:index' %}>Home</a>
    > Gigs
{% endblock %}

{% block content %}
    <h2>Gigs</h2>

    <a href={% url 'band:gig_create' %}>New Gig</a>

    <h3>Upcoming Gigs</h3>
    <ul>
        {% for gig in upcoming_gigs %}
            <li><a href={% url 'band:gig_detail' gig.id %}>{{ gig }} ({{ gig.start_datetime|localtime|date:"Y-m-d" }})</a></li>
        {% endfor %}
    </ul>

    <h3>Past Gigs</h3>
    <ul>
        {% for gig in past_gigs %}
            <li><a href={% url 'band:gig_detail' gig.id %}>{{ gig }} ({{ gig.start_datetime|localtime|date:"Y-m-d" }})</a></li>
        {% endfor %}
    </ul>

    {% if page_obj.paginator.num_pages > 1 %}
        <p>
            {% if page_obj.has_previous %}
                <a href="?page={{ page_obj.previous_page_number }}">Previous</a>
            {% endif %}
            {% for num in page_obj.paginator.page_range %}
                {% if num == page_obj.number %}
                    <strong>{{ num }}</strong>
                {% else %}
                    <a href="?page={{ num }}">{{ num }}</a>
                {% endif %}
            {% endfor %}
            {% if page_obj.has_next %}
                <a href="?page={{ page_obj.next_page_number }}">Next</a>
            {% endif %}
        </p>
    {% endif %}
{% endblock %}
```

Note: this also removes the stray `qq` after `{% load tz %}`.

- [ ] **Step 4: Run full pagination test case**

Run:

```bash
cd prsb && poetry run python manage.py test band.tests.GigListPastPaginationTestCase -v 2
```

Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add prsb/band/templates/band/gig_list.html prsb/band/tests.py
git commit -m "$(cat <<'EOF'
Add past-gigs pagination controls on the /gigs list.

EOF
)"
```

---

## Spec coverage check

| Spec requirement | Task |
|---|---|
| 10 per page, newest first | Task 1 |
| `?page=N` + `get_page` for bad pages | Task 1 |
| Upcoming unchanged | Task 1 |
| Prev + all numbers + Next; hide when ≤1 page | Task 2 |
| Fix `{% load tz %}qq` | Task 2 |
| Tests: size, page 2, invalid page | Task 1 |
| Tests: controls presence/absence | Task 2 |
