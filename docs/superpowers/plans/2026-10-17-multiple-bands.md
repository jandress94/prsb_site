# Multiple Bands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one site hold multiple bands that share songs, people, and instruments, with per-entry staffing on multi-band gigs.

**Architecture:** Add `Band`, `BandMembership`, and `BandSong`. Put the current band in `/bands/<slug>/` and filter roster, repertoire, gigs, and reports through that slug. Keep part knowledge on the person. Solve each setlist entry separately, with a non-preferred-player penalty that loses to a missing part and beats instrument fit.

**Tech Stack:** Django 5, Django TestCase, NumPy/SciPy MILP (`prsb/scripts/gig_part_assignment.py`), Poetry.

## Global Constraints

- Any logged-in user can open any band. Membership does not restrict viewing.
- Songs and people are many-to-many with bands. Instruments stay global. Part assignments stay on the person.
- `in_gig_rotation` lives on `BandSong`, not `Song`.
- A gig has at least one band and appears under every band on it.
- Staffing is per `GigSetlistEntry`. A one-band gig stores no mode. Multi-band song entries are `only`, `prefer`, or `any`.
- Deactivate bands, memberships, and song links. Do not rewrite existing gigs, attendance, overrides, or untouched setlist entries.
- Inactive band URLs still open. They cannot create songs, memberships, or gigs.
- Solver eligibility is stored `AVAILABLE` attendance plus any membership row, active or inactive. `maybe_available` stays unused.
- Prefer is one solve: fewest missing parts, then fewest non-preferred players, then the existing fit, workload, and tie-break scores.
- Solve single-band pools before `prefer` and `any`. Within a group, fewer eligible `PartAssignment` rows come first.
- Old unscoped content URLs are removed with no redirects.
- Band-scoped roles, `maybe_available` in the solver, and a part-assignment page redesign are out of scope.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `prsb/band/models.py` | `Band`, `BandMembership`, `BandSong`, gig bands, staffing fields, band-owned cover players and special dates |
| `prsb/band/migrations/0034_multiple_bands.py` | Schema and PRSB data migration |
| `prsb/band/admin.py` | Admin for the new models and delete protection |
| `prsb/band/scoping.py` | Band lookup, active-link helpers, gig pool membership |
| `prsb/band/validation.py` | Gig band and setlist staffing rules |
| `prsb/band/urls.py` | Global routes plus `/bands/<slug>/...` |
| `prsb/band/views.py` | Band-scoped querysets, create/link/deactivate actions, gig form |
| `prsb/band/templates/band/` | Band switcher, rotation display, staffing controls |
| `prsb/scripts/gig_part_assignment.py` | Per-entry solve order and prefer penalty |
| `prsb/scripts/coverage_risk.py` | Band-scoped roster, rotation songs, and lookback gigs |
| `prsb/band/dietary_restrictions.py` | Band-scoped roster; ignore a gig outside the band |
| `prsb/scripts/birthday_checker.py` | Special dates grouped by band |
| `prsb/scripts/create_db_objects.py` | Seed `BandSong.in_gig_rotation` |
| `prsb/band/tests.py` | Model, page, validation, and solver tests |
| `README.md` | Remove the shipped "Multiple bands" idea |

---

### Task 1: Band models and admin

**Files:**
- Modify: `prsb/band/models.py`
- Modify: `prsb/band/admin.py`
- Create: `prsb/band/migrations/0034_multiple_bands.py` (schema only in this task; data migration is Task 2)
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `BandMember`, `Song`, `Gig`, `GigSetlistEntry`, `DrumKitCoverPlayer`, `BandSpecialDate`
- Produces:
  - `Band(name: str, slug: str, is_active: bool)`
  - `BandMembership(band, member, is_active)` unique `(band, member)`
  - `BandSong(band, song, in_gig_rotation: bool, is_active: bool)` unique `(band, song)`
  - `Gig.bands` M2M to `Band`
  - `GigSetlistEntry.staffing_mode` in `{"", "only", "prefer", "any"}`
  - `GigSetlistEntry.staffing_band` nullable FK to `Band`
  - `DrumKitCoverPlayer.band` FK; unique `(band, member)`
  - `BandSpecialDate.band` FK
  - `Band.delete()` raises `ValidationError` when `gig_set` exists or a setlist entry names the band

- [ ] **Step 1: Write the failing model tests**

Add to `prsb/band/tests.py`:

```python
class BandModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="band_model_user", first_name="Ada", last_name="Brass")
        cls.song = Song.objects.create(title="Band Model Song")
        cls.gig = Gig.objects.create(
            name="Band Model Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )

    def test_membership_and_song_links_are_unique(self):
        band = Band.objects.create(name="River City", slug="river-city")
        BandMembership.objects.create(band=band, member=self.user.bandmember)
        BandSong.objects.create(band=band, song=self.song, in_gig_rotation=True)
        with self.assertRaises(Exception):
            BandMembership.objects.create(band=band, member=self.user.bandmember)
        with self.assertRaises(Exception):
            BandSong.objects.create(band=band, song=self.song)

    def test_band_delete_refused_while_named_by_gig(self):
        band = Band.objects.create(name="Host", slug="host")
        self.gig.bands.add(band)
        with self.assertRaises(ValidationError):
            band.delete()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd prsb && poetry run python manage.py test band.tests.BandModelTestCase`

Expected: FAIL because `Band` is not defined.

- [ ] **Step 3: Add the models**

In `prsb/band/models.py`, add `Band`, `BandMembership`, and `BandSong` after the imports. Remove `in_gig_rotation` from `Song` only in Task 3; leave it in place here.

```python
class Band(models.Model):
    name = models.CharField(max_length=256)
    slug = models.SlugField(max_length=256, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        if self.gig_set.exists() or GigSetlistEntry.objects.filter(staffing_band=self).exists():
            raise ValidationError("Deactivate this band instead of deleting it while gigs still use it.")
        return super().delete(*args, **kwargs)


class BandMembership(models.Model):
    band = models.ForeignKey(Band, on_delete=models.CASCADE)
    member = models.ForeignKey(BandMember, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["band", "member"], name="unique_band_membership")
        ]


class BandSong(models.Model):
    band = models.ForeignKey(Band, on_delete=models.CASCADE)
    song = models.ForeignKey(Song, related_name="band_songs", on_delete=models.CASCADE)
    in_gig_rotation = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["band", "song"], name="unique_band_song")
        ]
```

On `Gig`:

```python
bands = models.ManyToManyField("Band", related_name="gig_set", blank=True)
```

On `GigSetlistEntry`:

```python
staffing_mode = models.CharField(max_length=16, blank=True, default="")
staffing_band = models.ForeignKey(
    "Band", null=True, blank=True, on_delete=models.PROTECT, related_name="staffed_entries",
)
```

Change `DrumKitCoverPlayer.member` from `OneToOneField` to `ForeignKey`, add `band = models.ForeignKey(Band, on_delete=models.CASCADE)`, and replace the implicit unique member with `UniqueConstraint(fields=["band", "member"], name="unique_drum_kit_cover_player")`.

Add `band = models.ForeignKey(Band, on_delete=models.CASCADE)` to `BandSpecialDate`.

Register `Band`, `BandMembership`, and `BandSong` in `prsb/band/admin.py` with `list_display` including `is_active`. Show `band` on `DrumKitCoverPlayerAdmin` and `BandSpecialDate`. Override `BandAdmin.delete_model` and `delete_queryset` to call `obj.delete()` so the model guard surfaces in admin.

- [ ] **Step 4: Make the schema migration**

Run: `cd prsb && poetry run python manage.py makemigrations band --name multiple_bands`

The new FKs on `DrumKitCoverPlayer` and `BandSpecialDate` are non-null. This migration will not apply to a database that already has those rows. Task 2 replaces the generated operations with nullable adds, a data backfill, then alters them to non-null. Do not run `migrate` against the dev database until Task 2 is in the same migration file.

- [ ] **Step 5: Run the model tests**

Run: `cd prsb && poetry run python manage.py test band.tests.BandModelTestCase`

Expected: PASS. The test database applies the migration; existing cover-player and special-date tables are empty there.

- [ ] **Step 6: Commit**

```bash
git add prsb/band/models.py prsb/band/admin.py prsb/band/migrations/0034_multiple_bands.py prsb/band/tests.py
git commit -m "Add band, membership, and repertoire models."
```

---

### Task 2: Backfill one PRSB band

**Files:**
- Modify: `prsb/band/migrations/0034_multiple_bands.py`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: Task 1 models
- Produces: an active `Band` named `PRSB` with slug `prsb`, active links from every existing song, member, and gig, rotation copied from `Song.in_gig_rotation`, and existing cover players and special dates attached to that band

- [ ] **Step 1: Write the failing backfill test**

```python
class PrsbBackfillMigrationTestCase(TestCase):
    def test_historical_rows_attach_to_prsb(self):
        band = Band.objects.get(slug="prsb")
        self.assertTrue(band.is_active)
        self.assertEqual(band.name, "PRSB")
        self.assertEqual(BandSong.objects.filter(band=band, is_active=True).count(), Song.objects.count())
        self.assertEqual(BandMembership.objects.filter(band=band, is_active=True).count(), BandMember.objects.count())
        self.assertFalse(Gig.objects.exclude(bands=band).exists())
```

Create one song, user, and gig inside the test before the assertions. The migration itself runs before the test body, so this test proves new test rows are not required for the band to exist. Assert the band exists even when the test creates nothing:

```python
def test_prsb_band_exists_after_migrate(self):
    band = Band.objects.get(slug="prsb")
    self.assertEqual(band.name, "PRSB")
    self.assertTrue(band.is_active)
```

- [ ] **Step 2: Run it and confirm failure if the data operation is absent**

Run: `cd prsb && poetry run python manage.py test band.tests.PrsbBackfillMigrationTestCase`

Expected: FAIL with `Band matching query does not exist` until the data operation is added.

- [ ] **Step 3: Add the data operation**

Edit `0034_multiple_bands.py` so `DrumKitCoverPlayer.band` and `BandSpecialDate.band` are added nullable. Then add `RunPython`:

```python
def attach_prsb(apps, schema_editor):
    Band = apps.get_model("band", "Band")
    BandSong = apps.get_model("band", "BandSong")
    BandMembership = apps.get_model("band", "BandMembership")
    Song = apps.get_model("band", "Song")
    BandMember = apps.get_model("band", "BandMember")
    Gig = apps.get_model("band", "Gig")
    DrumKitCoverPlayer = apps.get_model("band", "DrumKitCoverPlayer")
    BandSpecialDate = apps.get_model("band", "BandSpecialDate")
    band, _ = Band.objects.get_or_create(slug="prsb", defaults={"name": "PRSB", "is_active": True})
    for song in Song.objects.all():
        BandSong.objects.get_or_create(
            band=band, song=song,
            defaults={"in_gig_rotation": song.in_gig_rotation, "is_active": True},
        )
    for member in BandMember.objects.all():
        BandMembership.objects.get_or_create(band=band, member=member, defaults={"is_active": True})
    for gig in Gig.objects.all():
        gig.bands.add(band)
    DrumKitCoverPlayer.objects.filter(band__isnull=True).update(band=band)
    BandSpecialDate.objects.filter(band__isnull=True).update(band=band)


def detach_prsb(apps, schema_editor):
    Band = apps.get_model("band", "Band")
    Band.objects.filter(slug="prsb").delete()
```

After `RunPython(attach_prsb, detach_prsb)`, alter both FKs to `null=False`. Keep `Song.in_gig_rotation` until Task 3.

- [ ] **Step 4: Run the backfill test and the existing suite**

Run: `cd prsb && poetry run python manage.py test band.tests.PrsbBackfillMigrationTestCase band.tests`

Expected: PASS. Existing behavior still reads `Song.in_gig_rotation`.

- [ ] **Step 5: Commit**

```bash
git add prsb/band/migrations/0034_multiple_bands.py prsb/band/tests.py
git commit -m "Backfill existing data onto the PRSB band."
```

---

### Task 3: Move rotation onto the song-band link

**Files:**
- Modify: `prsb/band/models.py`
- Modify: `prsb/band/admin.py`
- Modify: `prsb/band/views.py`
- Modify: `prsb/band/templates/band/song_detail.html`
- Modify: `prsb/scripts/gig_part_assignment.py`
- Modify: `prsb/scripts/coverage_risk.py`
- Modify: `prsb/scripts/create_db_objects.py`
- Modify: `prsb/band/tests.py`
- Create: `prsb/band/migrations/0035_remove_song_in_gig_rotation.py`

**Interfaces:**
- Consumes: `BandSong.in_gig_rotation`
- Produces: no `Song.in_gig_rotation` field. Solver, coverage risk, setlist picker, and song forms read `BandSong`.

- [ ] **Step 1: Write the failing rotation test**

```python
class BandSongRotationTestCase(TestCase):
    def test_rotation_is_per_band(self):
        song = Song.objects.create(title="Shared Tune")
        first = Band.objects.create(name="First", slug="first")
        second = Band.objects.create(name="Second", slug="second")
        BandSong.objects.create(band=first, song=song, in_gig_rotation=True)
        BandSong.objects.create(band=second, song=song, in_gig_rotation=False)
        self.assertTrue(song.band_songs.get(band=first).in_gig_rotation)
        self.assertFalse(song.band_songs.get(band=second).in_gig_rotation)
        self.assertFalse(hasattr(song, "in_gig_rotation"))
```

`hasattr` on a Django model can be true for a deferred field. Assert the field is gone from `_meta`:

```python
self.assertNotIn("in_gig_rotation", {field.name for field in Song._meta.fields})
```

- [ ] **Step 2: Run it and confirm failure**

Run: `cd prsb && poetry run python manage.py test band.tests.BandSongRotationTestCase`

Expected: FAIL because `Song.in_gig_rotation` still exists.

- [ ] **Step 3: Point readers at `BandSong` and remove the field**

Remove `in_gig_rotation` from `Song`, `SongAdmin.list_display`, `SongUpdateView.fields`, and `SongCreateView.fields`.

In `GigSetlistEntryForm.__init__`, replace the rotation filter with active links for the gig's bands:

```python
self.fields["song"].queryset = Song.objects.filter(
    band_songs__band__in=gig.bands.all(),
    band_songs__is_active=True,
).distinct()
```

Pass `gig` into the form `__init__`. `GigSetlistEntryFormSet` must use a form class closed over that gig.

In `gig_part_assignment.py`, delete both `song_part__song__in_gig_rotation=True` filters. Setlist entries are solved in Task 7; until then, keep one temporary filter so the current suite still means "PRSB rotation":

```python
song_part__song__band_songs__band__slug="prsb",
song_part__song__band_songs__in_gig_rotation=True,
song_part__song__band_songs__is_active=True,
```

Apply the same predicate in `coverage_risk.py` where it filters `in_gig_rotation=True`. Task 8 replaces the hardcoded slug with the current band.

In `create_db_objects.py`, create or update `BandSong(band=prsb, song=song, in_gig_rotation=row.InGigRotation, is_active=True)` after the song is saved. Look up PRSB with `Band.objects.get(slug="prsb")`.

In `song_detail.html`, replace `{{ song.in_gig_rotation }}` with the current band's link. Task 4 supplies `band_song` in context. For this task, guard it:

```html
<p>In Gig Rotation: {{ band_song.in_gig_rotation }}</p>
```

Update every `Song.objects.create(..., in_gig_rotation=...)` in `prsb/band/tests.py`. The call sites are the `in_gig_rotation=` lines in `SongHasSoloUpdateTestCase`, `SoloAwareAssignmentTestCase`, `GigPartAssignmentSoloMarkerTestCase`, `MemberDetailSoloColumnTestCase`, `GigRecommendationGroupingTestCase`, `GigPartAssignmentOverrideTestCase`, `PartAssignmentOrderingTestCase`, `CoverageRiskScoringTestCase`, `CoverageRiskViewsTestCase`, `SoloFieldsTestCase`, `PartAssignmentCanSoloFormTestCase`, `DrumKitCoverInjectionTestCase`, and `DrumKitCoverGigAssignmentTestCase`.

Pattern for each site:

```python
song = Song.objects.create(title="Solo Prefer Song")
BandSong.objects.create(
    band=Band.objects.get(slug="prsb"),
    song=song,
    in_gig_rotation=True,
)
```

`SongHasSoloUpdateTestCase.test_song_update_can_set_has_solo` posts `"in_gig_rotation": "on"`. Keep that key in the POST for now only if the form still has the field. After this task the form does not, so remove that key from the POST. The view test still asserts `has_solo`.

Generate the migration:

```bash
cd prsb && poetry run python manage.py makemigrations band --name remove_song_in_gig_rotation
```

- [ ] **Step 4: Run the suite**

Run: `cd prsb && poetry run python manage.py test band.tests`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prsb/band prsb/scripts/gig_part_assignment.py prsb/scripts/coverage_risk.py prsb/scripts/create_db_objects.py
git commit -m "Store gig rotation on the band-song link."
```

---

### Task 4: Band URLs and switcher

**Files:**
- Create: `prsb/band/scoping.py`
- Modify: `prsb/band/urls.py`
- Modify: `prsb/band/views.py`
- Modify: `prsb/band/templates/band/base.html`
- Modify: `prsb/band/templates/band/index.html`
- Modify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `Band.slug`, `Band.is_active`
- Produces:
  - `scoping.get_band(slug) -> Band` raising `Http404` for an unknown slug
  - `scoping.active_bands() -> QuerySet[Band]`
  - URL name `band:band_home` at `bands/<slug>/`
  - URL name `band:home` at ``, redirecting to the only active band
  - `request.band` on band-scoped views
  - context variables `band` and `active_bands` on those views

- [ ] **Step 1: Write the failing routing tests**

```python
class BandRoutingTestCase(TestCase):
    def test_one_active_band_redirects_home(self):
        response = self.client.get(reverse("band:home"))
        self.assertRedirects(response, reverse("band:band_home", kwargs={"slug": "prsb"}))

    def test_inactive_band_is_hidden_but_url_opens(self):
        Band.objects.create(name="Old", slug="old", is_active=False)
        home = self.client.get(reverse("band:band_home", kwargs={"slug": "prsb"}))
        self.assertNotContains(home, reverse("band:band_home", kwargs={"slug": "old"}))
        opened = self.client.get(reverse("band:band_home", kwargs={"slug": "old"}))
        self.assertEqual(opened.status_code, 200)

    def test_unknown_band_is_404(self):
        response = self.client.get("/bands/missing/")
        self.assertEqual(response.status_code, 404)

    def test_old_gig_list_is_gone(self):
        response = self.client.get("/gigs/")
        self.assertEqual(response.status_code, 404)

    def test_gig_is_listed_under_every_band_on_it(self):
        other = Band.objects.create(name="Guest", slug="guest")
        gig = Gig.objects.create(
            name="Shared Gig",
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=2),
        )
        prsb = Band.objects.get(slug="prsb")
        gig.bands.add(prsb, other)
        for slug in ("prsb", "guest"):
            response = self.client.get(reverse("band:gig_list", kwargs={"slug": slug}))
            self.assertContains(response, "Shared Gig")
        outsider = Band.objects.create(name="Outsider", slug="outsider")
        missing = self.client.get(reverse("band:gig_detail", kwargs={"slug": outsider.slug, "pk": gig.pk}))
        self.assertEqual(missing.status_code, 404)
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `cd prsb && poetry run python manage.py test band.tests.BandRoutingTestCase`

Expected: FAIL because `band:home` and `band:band_home` do not exist.

- [ ] **Step 3: Add the routes**

`prsb/band/scoping.py`:

```python
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Band


def active_bands():
    return Band.objects.filter(is_active=True)


def get_band(slug):
    return get_object_or_404(Band, slug=slug)
```

Replace the unscoped content routes in `prsb/band/urls.py`. Keep login, logout, password, profile, instruments, health, and `api/instruments/` at the root. Move index, members, songs, part assignments, gigs, and reports under `bands/<slug>/`.

```python
path("", views.home, name="home"),
path("bands/<slug:slug>/", views.BandHomeView.as_view(), name="band_home"),
path("bands/<slug:slug>/members/", views.MemberListView.as_view(), name="member_list"),
```

Repeat that prefix for every content route currently declared without it. Pass `slug` through. Gig routes use `bands/<slug:slug>/gigs/<int:pk>/`.

```python
def home(request):
    bands = list(active_bands())
    if len(bands) == 1:
        return redirect("band:band_home", slug=bands[0].slug)
    return render(request, "band/home.html", {"bands": bands})
```

Add `BandScopedMixin` in `views.py` and put it on every band-scoped view:

```python
class BandScopedMixin:
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.band = get_band(kwargs["slug"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["band"] = self.band
        context["active_bands"] = active_bands()
        return context

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and not self.band.is_active and request.resolver_match.url_name in {
            "song_create", "member_link", "gig_create",
        }:
            raise PermissionDenied("An inactive band cannot create new records.")
        return super().dispatch(request, *args, **kwargs)
```

`GigListView.get_context_data` filters both upcoming and past gigs with `.filter(bands=self.band)` before ordering and pagination.

`Gig` views that receive `pk` must 404 when `self.band` is not in `gig.bands`.

```python
def get_object(self):
    gig = super().get_object()
    if not gig.bands.filter(pk=self.band.pk).exists():
        raise Http404
    return gig
```

Add the same check to function-style gig views that load by `gig_id`.

Create `prsb/band/templates/band/home.html` with a link per active band. In `base.html`, above `{% block nav %}`, render a switcher when `active_bands` is present:

```html
{% if active_bands %}
<p>
    {% for switch_band in active_bands %}
        <a href="{% url 'band:band_home' switch_band.slug %}">{{ switch_band.name }}</a>
    {% endfor %}
</p>
{% endif %}
```

Update every `{% url 'band:...' %}` in band templates to pass `band.slug`. Update every `reverse("band:...")` in `views.py` and `tests.py` to include `slug`. Existing tests use PRSB: `kwargs={"slug": "prsb", ...}`.

- [ ] **Step 4: Run the suite**

Run: `cd prsb && poetry run python manage.py test band.tests`

Expected: PASS. Profile, login, and instruments still resolve without a slug.

- [ ] **Step 5: Commit**

```bash
git add prsb/band
git commit -m "Scope band pages under the band slug."
```

---

### Task 5: Roster, repertoire, and part assignments

**Files:**
- Modify: `prsb/band/views.py`
- Modify: `prsb/band/urls.py`
- Modify: `prsb/band/templates/band/song_list.html`
- Modify: `prsb/band/templates/band/song_detail.html`
- Modify: `prsb/band/templates/band/song_update_form.html`
- Modify: `prsb/band/templates/band/bandmember_list.html`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `BandMembership`, `BandSong`, `BandScopedMixin`
- Produces:
  - `MemberListView.get_queryset` returns active memberships for `self.band`
  - `SongListView.get_queryset` returns songs with an active `BandSong` for `self.band`
  - `PartAssignmentListView.get_queryset` filters `song_part__song__band_songs__band=self.band` and `band_songs__is_active=True`
  - `link_member(band, member)` and `link_song(band, song, in_gig_rotation=False)` create or reactivate the row
  - song create also creates an active `BandSong` for the current band
  - song update writes rotation only to the current band's `BandSong`

- [ ] **Step 1: Write the failing page tests**

```python
class BandPageScopeTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prsb = Band.objects.get(slug="prsb")
        cls.other = Band.objects.create(name="Other", slug="other")
        cls.user = User.objects.create_user(username="scope_user", first_name="Scope", last_name="User")
        BandMembership.objects.create(band=cls.prsb, member=cls.user.bandmember)
        cls.song = Song.objects.create(title="Scoped Song")
        BandSong.objects.create(band=cls.prsb, song=cls.song, in_gig_rotation=True)

    def test_other_band_hides_prsb_roster_and_song(self):
        self.assertNotContains(
            self.client.get(reverse("band:member_list", kwargs={"slug": "other"})),
            "Scope User",
        )
        self.assertNotContains(
            self.client.get(reverse("band:song_list", kwargs={"slug": "other"})),
            "Scoped Song",
        )

    def test_deactivate_hides_member_and_link_reactivates(self):
        membership = BandMembership.objects.get(band=self.prsb, member=self.user.bandmember)
        membership.is_active = False
        membership.save()
        listed = self.client.get(reverse("band:member_list", kwargs={"slug": "prsb"}))
        self.assertNotContains(listed, "Scope User")
        self.client.post(
            reverse("band:member_link", kwargs={"slug": "prsb"}),
            {"member": self.user.bandmember.pk},
        )
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)

    def test_song_title_edit_is_global_and_rotation_is_local(self):
        BandSong.objects.create(band=self.other, song=self.song, in_gig_rotation=False)
        self.client.post(
            reverse("band:song_update", kwargs={"slug": "prsb", "pk": self.song.pk}),
            {"title": "Renamed", "composer": "", "duration": "", "form": "", "in_gig_rotation": "on",
             "parts-TOTAL_FORMS": "0", "parts-INITIAL_FORMS": "0", "parts-MIN_NUM_FORMS": "0",
             "parts-MAX_NUM_FORMS": "1000"},
        )
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, "Renamed")
        self.assertFalse(self.song.band_songs.get(band=self.other).in_gig_rotation)
```

Also assert an inactive band POST to `song_create`, `member_link`, and `gig_create` returns 403.

- [ ] **Step 2: Run the tests and confirm failure**

Run: `cd prsb && poetry run python manage.py test band.tests.BandPageScopeTestCase`

Expected: FAIL because the list views are still global and `member_link` is missing.

- [ ] **Step 3: Filter the pages and add link/deactivate actions**

```python
def link_member(band, member):
    row, _ = BandMembership.objects.get_or_create(band=band, member=member)
    if not row.is_active:
        row.is_active = True
        row.save(update_fields=["is_active"])
    return row


def link_song(band, song, in_gig_rotation=False):
    row, created = BandSong.objects.get_or_create(
        band=band, song=song, defaults={"in_gig_rotation": in_gig_rotation, "is_active": True},
    )
    if not created and not row.is_active:
        row.is_active = True
        row.in_gig_rotation = in_gig_rotation
        row.save(update_fields=["is_active", "in_gig_rotation"])
    return row
```

`MemberListView.get_queryset`:

```python
return BandMember.objects.filter(
    bandmembership__band=self.band, bandmembership__is_active=True,
)
```

`SongListView.get_queryset`:

```python
return Song.objects.filter(band_songs__band=self.band, band_songs__is_active=True)
```

`PartAssignmentListView.get_queryset` adds:

```python
song_part__song__band_songs__band=self.band,
song_part__song__band_songs__is_active=True,
```

`SongCreateView.form_valid` saves the song, calls `link_song(self.band, self.object, in_gig_rotation=form.cleaned_data["in_gig_rotation"])`, and redirects to the band-scoped song detail. Add `in_gig_rotation` as a form field that is not a `Song` model field.

`SongUpdateView` loads the current `BandSong`. Its extra `in_gig_rotation` field saves only that row. Title, composer, duration, form, and parts still save on `Song`.

`SongDetailView` adds `band_song` and `other_bands = Band.objects.filter(bandsong__song=song, bandsong__is_active=True).exclude(pk=self.band.pk)`.

Add POST views:

- `member_link` calls `link_member`
- `member_unlink` sets that membership `is_active=False`
- `song_link` calls `link_song`
- `song_unlink` sets that `BandSong.is_active=False`

None of those views delete gigs, attendance, or setlist entries.

Put a member `<select>` and song `<select>` on the band pages, plus a deactivate button on each row. The song `<select>` lists songs without an active link to this band.

- [ ] **Step 4: Run the suite**

Run: `cd prsb && poetry run python manage.py test band.tests`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prsb/band
git commit -m "Filter each band's roster and repertoire."
```

---

### Task 6: Gig bands, staffing, and validation

**Files:**
- Create: `prsb/band/validation.py`
- Modify: `prsb/band/views.py`
- Modify: `prsb/band/templates/band/gig_update_form.html`
- Modify: `prsb/band/templates/band/gig_setlist_update.html`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `Gig.bands`, `GigSetlistEntry.staffing_mode`, `GigSetlistEntry.staffing_band`
- Produces:
  - `validate_gig_bands(gig, new_bands, entries) -> None` raises `ValidationError`
  - `validate_entry_change(entry, previous, gig_bands) -> None` raises `ValidationError`
  - `entry_pool_member_ids(entry) -> set[int]`
  - `GigForm.bands` required, initial band is the current band on create
  - setlist form fields `staffing_mode` and `staffing_band`, shown only when the gig has two or more bands

- [ ] **Step 1: Write the failing validation tests**

```python
class GigStaffingValidationTestCase(TestCase):
    def test_multi_band_entries_can_name_different_bands(self):
        gig = self.make_two_band_gig()
        first, second = gig.bands.order_by("name")
        song_a = self.link_song(first, "Song A")
        song_b = self.link_song(second, "Song B")
        GigSetlistEntry.objects.create(gig=gig, song=song_a, staffing_mode="only", staffing_band=first, _order=0)
        GigSetlistEntry.objects.create(gig=gig, song=song_b, staffing_mode="only", staffing_band=second, _order=1)
        self.assertEqual(gig.gigsetlistentry_set.count(), 2)

    def test_removing_named_band_fails(self):
        gig = self.make_two_band_gig()
        first, second = list(gig.bands.all())
        song = self.link_song(first, "Kept")
        entry = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=first,
        )
        with self.assertRaises(ValidationError):
            validate_gig_bands(gig, [second], [entry])

    def test_deactivating_song_link_leaves_old_entry_valid(self):
        gig = self.make_two_band_gig()
        first = gig.bands.order_by("name").first()
        song = self.link_song(first, "Old")
        entry = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=first,
        )
        BandSong.objects.filter(band=first, song=song).update(is_active=False)
        validate_entry_change(entry, previous=entry, gig_bands=set(gig.bands.all()))

    def test_new_only_entry_requires_active_repertoire(self):
        gig = self.make_two_band_gig()
        first, second = list(gig.bands.order_by("name"))
        song = Song.objects.create(title="Unlinked")
        entry = GigSetlistEntry(gig=gig, song=song, staffing_mode="only", staffing_band=first)
        with self.assertRaises(ValidationError):
            validate_entry_change(entry, previous=None, gig_bands=set(gig.bands.all()))
```

Build `make_two_band_gig` inside the test case. It creates two active bands, links both to a gig, and returns the gig. `link_song` creates an active `BandSong`.

Add a view test that a one-band gig save clears `staffing_mode` and `staffing_band`, and a new override whose member is outside the entry pool returns a validation error.

- [ ] **Step 2: Run the tests and confirm failure**

Run: `cd prsb && poetry run python manage.py test band.tests.GigStaffingValidationTestCase`

Expected: FAIL because `validate_gig_bands` is not defined.

- [ ] **Step 3: Implement validation and the forms**

`prsb/band/validation.py`:

```python
from django.core.exceptions import ValidationError

from .models import BandMembership, BandSong


MODES_WITH_BAND = {"only", "prefer"}


def validate_gig_bands(gig, new_bands, entries):
    if not new_bands:
        raise ValidationError("A gig needs at least one band.")
    new_ids = {band.pk for band in new_bands}
    for entry in entries:
        if entry.song_id is None:
            continue
        if entry.staffing_band_id and entry.staffing_band_id not in new_ids:
            raise ValidationError("A setlist entry still names a band that would be removed.")
        if not BandSong.objects.filter(song_id=entry.song_id, band_id__in=new_ids).exists():
            raise ValidationError("A setlist song would no longer belong to any remaining band.")


def validate_entry_change(entry, previous, gig_bands):
    if entry.song_id is None:
        if entry.staffing_mode or entry.staffing_band_id:
            raise ValidationError("A break has no staffing mode.")
        return
    if len(gig_bands) < 2:
        return
    changed = previous is None or any(
        getattr(previous, attr) != getattr(entry, attr)
        for attr in ("song_id", "staffing_mode", "staffing_band_id")
    )
    if not changed:
        return
    band_ids = {band.pk for band in gig_bands}
    if entry.staffing_mode == "any":
        if entry.staffing_band_id:
            raise ValidationError("Any band on the gig does not name a band.")
        if not BandSong.objects.filter(song_id=entry.song_id, band_id__in=band_ids, is_active=True).exists():
            raise ValidationError("The song must be active in a band on the gig.")
        return
    if entry.staffing_mode not in MODES_WITH_BAND or entry.staffing_band_id not in band_ids:
        raise ValidationError("Only and Prefer must name a band on the gig.")
    band = entry.staffing_band
    if not band.is_active:
        raise ValidationError("New staffing must name an active band.")
    if not BandSong.objects.filter(song_id=entry.song_id, band=band, is_active=True).exists():
        raise ValidationError("The song must be active in the named band.")


def entry_pool_member_ids(entry):
    gig_band_ids = set(entry.gig.bands.values_list("pk", flat=True))
    if entry.gig.bands.count() < 2 or entry.staffing_mode in {"", "only"}:
        band_ids = {entry.staffing_band_id} if entry.staffing_band_id else gig_band_ids
    elif entry.staffing_mode == "prefer":
        band_ids = gig_band_ids
    else:
        band_ids = gig_band_ids
    return set(BandMembership.objects.filter(band_id__in=band_ids).values_list("member_id", flat=True))
```

`GigForm` adds `bands = forms.ModelMultipleChoiceField(queryset=Band.objects.none())`. `__init__` receives `band` and sets the queryset to `Band.objects.filter(is_active=True) | Band.objects.filter(pk__in=current_ids)`. Create initial is `[band]`. `clean_bands` calls `validate_gig_bands`. When the cleaned set has one band, `form_valid` clears staffing fields on that gig's song entries after save.

`GigSetlistEntryForm` includes `staffing_mode` and `staffing_band`. Choices are `("", "---------")`, `("only", "Only this band")`, `("prefer", "Prefer this band")`, `("any", "Any band on the gig")`. The song queryset is active `BandSong` rows for the gig's bands. `clean` calls `validate_entry_change`. When the posted band count is one, ignore posted staffing and save blanks.

`GigPartAssignmentOverrideForm.clean` rejects a member not in `entry_pool_member_ids` for the selected song entry. Existing override rows are not rechecked during deactivation.

Dropping from two bands to one clears modes inside the gig form save, not by deleting entries.

`GigDetailView` and `GigAvailabilityUpdateView` keep attendance rows already stored on the gig, including people whose membership is now inactive. The "missing member" choices on those pages are active memberships of the gig's bands who do not yet have an attendance row. Saving attendance does not delete a row just because its member is inactive. A POST that adds a member outside that active set fails validation.

- [ ] **Step 4: Run the suite**

Run: `cd prsb && poetry run python manage.py test band.tests`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prsb/band
git commit -m "Validate per-song gig staffing."
```

---

### Task 7: Per-entry solver

**Files:**
- Modify: `prsb/scripts/gig_part_assignment.py`
- Modify: `prsb/band/templates/band/gig_part_assignments.html`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `entry_pool_member_ids`, `GigSetlistEntry.staffing_mode`, `BandMembership`, `DrumKitCoverPlayer.band`
- Produces:
  - `GigPartAssignment.setlist_entry`
  - `get_gig_song_part_assignments(..., preferred_member_ids: set[int] | None)`
  - setlist results in solve order internally, displayed one row per entry

- [ ] **Step 1: Write the failing solver tests**

Use two bands, one song part, one instrument with quantity 1, and two available members.

```python
class MultiBandSolverTestCase(TestCase):
    def test_only_never_assigns_the_other_band(self):
        gig, entry, preferred, other = self.scenario(mode="only")
        setlist, _, _ = get_gig_part_assignments(gig, [])
        members = {row.member for row in setlist[0].part_assignments}
        self.assertIn(preferred, members)
        self.assertNotIn(other, members)

    def test_prefer_fills_a_hole_with_the_other_band(self):
        gig, entry, preferred, other = self.scenario(mode="prefer", preferred_knows_part=False)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, other)

    def test_prefer_keeps_preferred_player_when_both_cover(self):
        gig, entry, preferred, other = self.scenario(mode="prefer", preferred_knows_part=True)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, preferred)

    def test_dual_member_counts_as_preferred(self):
        gig, entry, preferred, other = self.scenario(mode="prefer", preferred_knows_part=True)
        BandMembership.objects.create(band=entry.staffing_band, member=other)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, preferred)

    def test_any_may_use_either_roster(self):
        gig, entry, preferred, other = self.scenario(mode="any", preferred_knows_part=False)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, other)
```

`scenario` creates the gig, both memberships, both attendance rows as `AVAILABLE`, the part assignment rows, and the setlist entry. `preferred_knows_part=False` means only `other` has a `PartAssignment`.

Add one ordering test with three entries: an Only entry with 2 candidate rows, a Prefer entry with 1 candidate row, and an Only entry with 1 candidate row. Spy by assigning distinct songs and recording `member_song_counts` progression is unnecessary. Instead, give one shared player a part on all three songs and give the Prefer song an alternate non-preferred player. Assert the Only songs receive the shared player and the Prefer song receives the alternate. That fails if Prefer is solved first.

Add a test with the same song on two entries, Only band A and Only band B, and assert the two `GigPartAssignment` rows name those two members.

Add a test that deactivating the preferred member's `BandMembership` after the `AVAILABLE` attendance row exists still lets **Only** assign that member. The solver must not consult `is_active`.

Add a test that an out-of-rotation setlist song still returns an assignment, and that recommendations exclude songs already on the setlist and include an active in-rotation song for either gig band.

- [ ] **Step 2: Run the tests and confirm failure**

Run: `cd prsb && poetry run python manage.py test band.tests.MultiBandSolverTestCase`

Expected: FAIL because the solver still solves one pool per distinct song.

- [ ] **Step 3: Solve entries in constrained-first order**

Change `get_gig_song_part_assignments` to take `preferred_member_ids=None`. After `missing_part_penalty` is computed:

```python
fit_swing = soloist_reward + max_instrument_reward + 1
non_preferred_penalty = fit_swing
if preferred_member_ids is not None:
    missing_part_penalty = max(
        missing_part_penalty,
        num_assignments * non_preferred_penalty + 1,
    )
    c_missing_part_penalty[num_assignments + num_overrides:] = missing_part_penalty
    for index, assignment in enumerate(all_assignments):
        if assignment.member_id not in preferred_member_ids:
            c_instrument[index] += non_preferred_penalty
```

Raising the missing-part penalty above every non-preferred assignment keeps a filled part ahead of a hole. `fit_swing` keeps one preferred player ahead of a non-preferred player when both cover the part.

Replace the song loop in `get_gig_part_assignments`:

```python
def pool_rank(entry):
    if entry is None:
        return 3
    if entry.gig.bands.count() < 2 or entry.staffing_mode in {"", "only"}:
        return 0 if entry.gig.bands.count() < 2 else 1
    return 2
```

Build one work item per setlist song entry. Candidate rows are `PartAssignment` rows whose member is an available attendee, whose member id is in `entry_pool_member_ids(entry)`, and whose instrument is on the gig. Do not require rotation. For a recommendation, the pool is available attendees with a membership in any gig band, and the song must have an active in-rotation `BandSong` for a gig band and not already be on the setlist.

Sort work items by `(pool_rank(entry), candidate_count, song.title, entry._order if entry else 0)`.

For `prefer`, pass `preferred_member_ids` from memberships of `entry.staffing_band`, including inactive rows. For every other mode, pass `None`.

Drum-kit covers: **Only** and a one-band gig load `DrumKitCoverPlayer` for the staffing band, or the gig's only band. **Prefer** and **Any** load cover players for every gig band. If a member appears twice, keep the lower priority. Tie-break equal priority by band name. A cover player whose membership includes the preferred band is inside `preferred_member_ids`.

Add `setlist_entry` to `GigPartAssignment.__init__`. Append one result per entry. Display sort remains title, then `_order`, and must not collapse entries with the same song. Update `gig_part_assignments.html` only if it assumes one result per song id; iterate the result list as it already does for distinct songs.

Remove the temporary `band__slug="prsb"` filter added in Task 3.

- [ ] **Step 4: Run the suite**

Run: `cd prsb && poetry run python manage.py test band.tests`

Expected: PASS. Existing single-band solver tests still pass because their songs are linked to PRSB and their gigs have one band.

- [ ] **Step 5: Commit**

```bash
git add prsb/scripts/gig_part_assignment.py prsb/band/tests.py prsb/band/templates/band/gig_part_assignments.html
git commit -m "Solve multi-band setlist entries separately."
```

---

### Task 8: Reports and special dates

**Files:**
- Modify: `prsb/scripts/coverage_risk.py`
- Modify: `prsb/band/views.py`
- Modify: `prsb/band/dietary_restrictions.py`
- Modify: `prsb/scripts/birthday_checker.py`
- Test: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `Band`, `BandMembership.is_active`, `BandSong.in_gig_rotation`, `Gig.bands`
- Produces:
  - `get_coverage_risk(band: Band, lookback: int = DEFAULT_LOOKBACK) -> CoverageRiskReport`
  - `dietary_restriction_members(..., band: Band)`
  - `upcoming_gigs(band)` and `past_gigs(band)`
  - birthday messages grouped by band name

- [ ] **Step 1: Write the failing report tests**

```python
class BandReportScopeTestCase(TestCase):
    def test_coverage_risk_ignores_another_bands_rotation_song(self):
        other = Band.objects.create(name="Other", slug="other-report")
        song = Song.objects.create(title="Other Rotation")
        SongPart.objects.create(song=song, name="Lead")
        BandSong.objects.create(band=other, song=song, in_gig_rotation=True)
        report = get_coverage_risk(Band.objects.get(slug="prsb"))
        self.assertNotIn("Other Rotation", [row.song.title for row in report.songs])

    def test_dietary_gig_outside_the_band_falls_back_to_roster(self):
        other = Band.objects.create(name="Diet Other", slug="diet-other")
        gig = Gig.objects.create(
            name="Other Gig",
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=2),
        )
        gig.bands.add(other)
        resolved = resolve_gig(str(gig.pk), band=Band.objects.get(slug="prsb"))
        self.assertIsNone(resolved)
```

Extend `DietaryRestrictionsViewTestCase` requests to `reverse("band:dietary_restrictions", kwargs={"slug": "prsb"})`.

- [ ] **Step 2: Run the tests and confirm failure**

Run: `cd prsb && poetry run python manage.py test band.tests.BandReportScopeTestCase`

Expected: FAIL because `get_coverage_risk` does not take `band` and `resolve_gig` does not reject another band's gig.

- [ ] **Step 3: Scope the reports**

`get_coverage_risk(band, lookback=DEFAULT_LOOKBACK)`:

- `_lookback_gigs` adds `.filter(bands=band, is_small_group=False)`
- active members come from active `BandMembership` rows for `band` whose user `is_active` is true
- songs come from active `BandSong` rows for `band` with `in_gig_rotation=True`
- assignment queries use those song ids instead of `song__in_gig_rotation`

`CoverageRiskView.get_context_data` calls `get_coverage_risk(self.band, _parse_lookback(self.request))`.

`resolve_gig(raw, band)` returns `None` when the gig is missing or `band` is not on it. `dietary_restriction_members` adds `.filter(bandmembership__band=band, bandmembership__is_active=True)`. `upcoming_gigs(band)` and `past_gigs(band)` filter `bands=band`. The view passes `self.band`.

`birthday_checker.lambda_handler` builds one body section per band that has a birthday member with an active membership or a special date today:

```python
lines = [f"{band.name}", f"{member.user.get_full_name()}'s Birthday"]
```

Subject: `Special Day {d}` plus the band name when only one band has results. Include every matching band in the body. Do not send mail when no band has results.

- [ ] **Step 4: Run the suite**

Run: `cd prsb && poetry run python manage.py test band.tests`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prsb/scripts/coverage_risk.py prsb/scripts/birthday_checker.py prsb/band
git commit -m "Scope reports and special dates to a band."
```

---

### Task 9: Ideas log

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing
- Produces: the ideas list no longer contains the shipped "Multiple bands" bullet. The band-scoped roles bullet stays.

- [ ] **Step 1: Remove the shipped bullet**

Delete the line `- Multiple bands` from `README.md`. Leave the band-scoped roles line.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Remove the shipped multiple-bands idea."
```
