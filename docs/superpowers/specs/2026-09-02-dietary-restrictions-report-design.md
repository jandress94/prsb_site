# Dietary Restrictions Report

## Goal

Give signed-in members a report of active band members who have dietary restrictions, optionally limited to people expected at a selected gig. The data is personal, so the page requires authentication even though the rest of the site currently does not.

## Context

- `BandMember.dietary_restrictions` is a `TextField(blank=True)`. Empty string is the unset value; do not treat only SQL `NULL` as missing.
- Active members are those with `user__is_active=True` (same as the member list).
- Reports hub: `/reports/` (`ReportsView`, `band/templates/band/reports.html`). Coverage Risk is the only report today and is not login-gated.
- `GigAttendance.status` is `available` / `maybe_available` / `unavailable`. A member may have **no** row for a gig.
- `LoginRequiredMiddleware` exists but is commented out in `prsb/settings.py`. Do not turn it on for the whole site in this work.
- Login is at `/accounts/login/`. Django’s default `LOGIN_URL` already matches that path.

## Success criteria

- Anonymous visitors to the report are redirected to login; any signed-in member can view it.
- Default view: all active members with a non-empty dietary restriction (blank and whitespace-only excluded), ordered by first then last name.
- Optional gig filter; without a gig, attendance status controls are hidden or disabled and ignored if present in the query string.
- With a gig: include members whose attendance matches the selected statuses. Status options are **Available**, **Maybe available**, and **No status yet** (no `GigAttendance` row). There is no Unavailable option; unavailable members never appear in a gig-scoped result.
- Default statuses when a gig is selected: **Available** only.
- Gig picker: a native `<select>` with the gigs split into **Upcoming** and **Past** `<optgroup>`s (name + local date per option). “All active members” is the default option. No Select2 / Tom Select.
- Linked from the reports hub.

## Query params

| Param | Meaning |
|---|---|
| (none) | All active members with a restriction. Status params ignored. |
| `gig=<id>` | Scope to that gig. Unknown / non-numeric id → treat as no gig (default view), 200. |
| `status` (repeatable) | Only when `gig` is valid. Allowed values: `available`, `maybe_available`, `no_status`. Unknown values dropped. If the `status` key is **absent**, default to `available`. If it is **present** and no allowed values remain, the member list is empty. |

Example: `/reports/dietary-restrictions/?gig=12&status=available&status=no_status`

## Behavior

### Member set (always)

```
BandMember where user.is_active
and dietary_restrictions stripped of leading/trailing whitespace is non-empty
order by user.first_name, user.last_name
```

Use `django.db.models.functions.Trim` so empty and whitespace-only values are excluded in the queryset (SQLite tests and Postgres both support it).

### Gig scoping

If `gig` is a real gig:

- `available` → `GigAttendance` for that gig with `status=available`
- `maybe_available` → same with `maybe_available`
- `no_status` → no `GigAttendance` row for that gig
- Union of selected statuses
- `unavailable` is never included
- No allowed statuses selected → empty table (the filter matched nobody), not a silent fallback to Available

A first load or a link with only `?gig=<id>` omits `status`, so the default is still Available. The Apply form must send `status` whenever a gig is selected (hidden empty `status=` plus any checked boxes) so “all boxes unchecked” is distinguishable from “never specified.”

### Gig picker UI

A native `<select name="gig">`. No new JS libraries.

- First option: “All active members”, value `""` (no `gig` param).
- `<optgroup label="Upcoming">`: `end_datetime >= now`, ordered by `start_datetime` ascending (same as `GigListView`).
- `<optgroup label="Past">`: `end_datetime < now`, ordered by `start_datetime` descending.
- Each option label is `<name> — <local start date>` in `Y-m-d`.
- An `<optgroup>` is omitted entirely when it has no gigs.
- The selected gig renders `selected`, so the choice survives Apply whether it is upcoming or past.
- Fully functional with JS off; browsers provide type-ahead within the open dropdown.

An earlier revision used a search box over a radio list. It was replaced because the list was long and because the site stylesheet sets `label { display: inline-block }`, which overrides the `hidden` attribute the filter script relied on.

### Status controls

- Checkboxes: Available (`available`), Maybe available (`maybe_available`), No status yet (`no_status`). Default checked: Available.
- When “All active members” is selected (or no gig in the GET), the `<fieldset>` renders `disabled`. Disabled fields are not submitted, so the hidden `status=""` also drops out.
- Client-side: changing the gig `<select>` enables/disables the fieldset without a round trip. Server still ignores `status` when there is no valid gig.

### Page chrome

- URL: `/reports/dietary-restrictions/` (`band:dietary_restrictions`)
- Template: `band/templates/band/dietary_restrictions.html`
- Nav: Home > Reports > Dietary Restrictions
- Table: Member | Dietary restriction. Empty state copy when the filtered set is empty.
- Caption stating the current scope (all active vs gig name + human-readable statuses: Available, Maybe available, No status yet).

## Implementation

### View (`band/views.py`)

`DietaryRestrictionsView(LoginRequiredMixin, generic.TemplateView)`:

1. Parse `gig` and `status` as above.
2. Base queryset: active members with non-empty restrictions.
3. If gig valid, filter by status union.
4. Context: `members`, `gig` (or `None`), `statuses` (normalized list), `upcoming_gigs`, `all_gigs` (or a JSON-serializable list of `{id, name, date}` for search). Keep the view thin; a small helper in `views.py` or `band/` is fine. Do not add a `scripts/` module for this.

Name the existing login route `login` if `LoginRequiredMixin` / tests need `{% url 'login' %}` or `reverse("login")`. Do not enable `LoginRequiredMiddleware`.

### URLs and hub

- `path("reports/dietary-restrictions/", ..., name="dietary_restrictions")`
- Link on `reports.html` with a one-line description (personal data; login required).

### Tests (`band/tests.py`)

Anonymous GET → redirect to login (follow or check `302` + login URL).

Signed-in GET:

- Default: only active members with non-blank restrictions; inactive and blank/whitespace excluded.
- `?gig=<id>` with no `status` key: Available attendees only (unavailable, maybe, and no-row excluded).
- `?gig=<id>&status=` (or only junk such as `unavailable`): empty member list.
- `?gig=99999` or `?gig=abc`: same as no gig; status params ignored.
- `?status=no_status` with no gig: full default member set (status ignored).
- Page contains member names and restriction text for included rows only.

## Out of scope

- Site-wide login / RBAC
- Staff-only access
- Unavailable as a filter
- Select2 / Tom Select / new npm or CDN widgets
- Export / print-specific layout
- Showing dietary restrictions on public member detail (still commented out)
- T-shirt, birthday, or other reports
