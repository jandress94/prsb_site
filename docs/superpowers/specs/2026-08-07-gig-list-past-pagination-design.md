# Past Gigs Pagination on `/gigs`

## Goal

Paginate the past-gigs list on the gigs index so the page stays usable as the archive grows. Upcoming gigs stay fully listed.

## Context

`GigListView` currently loads all upcoming and past gigs into context with no pagination. `Gig` default ordering is `-start_datetime` (newest first). The project has no existing `Paginator` usage. Template: `band/gig_list.html`.

## Success criteria

- Past gigs show **10 per page**, newest first
- Navigation: Previous, all page numbers, Next (`?page=N` on `/gigs/`)
- Upcoming gigs unchanged (full list, ascending by start time)
- Invalid/`out-of-range` `page` params still return 200 with a valid page (`Paginator.get_page`)
- Controls hidden when there is only one page (or zero past gigs)
- Tests cover page size, page 2 slice, and invalid page handling

## Approach

Manual `Paginator` inside `GigListView.get_context_data` (not `ListView.paginate_by`), so upcoming and past keep separate querysets and template variable names.

## Behavior

| Section | Behavior |
|---|---|
| Upcoming | All gigs with `end_datetime >= now`, ordered by `start_datetime` ascending |
| Past | Gigs with `end_datetime < now`, model default order (`-start_datetime`), paginated |
| Page size | 10 |
| URL | `/gigs/?page=N` (page 1 may omit the query param) |
| Bad `page` | `paginator.get_page(page)` — non-numeric → page 1; too high → last page |
| Controls | Prev (if `has_previous`), link per page number (current page not a link), Next (if `has_next`). Show **all** page numbers (no ellipsis window). Omit the whole control block when `num_pages <= 1` |

## Implementation

### View (`band/views.py` — `GigListView`)

In `get_context_data`:

1. Keep `upcoming_gigs` as today.
2. Build past queryset: `Gig.objects.filter(end_datetime__lt=timezone.now())` (ordering from model `Meta`).
3. `paginator = Paginator(past_qs, 10)`.
4. `page_obj = paginator.get_page(self.request.GET.get("page"))`.
5. Context: `past_gigs = page_obj.object_list`, plus `page_obj` for template controls.

### Template (`band/templates/band/gig_list.html`)

- Keep existing upcoming / past list markup; iterate `past_gigs` as today.
- Below the past list, if `page_obj.paginator.num_pages > 1`, render pagination links using `?page={{ n }}` (and `page_obj.previous_page_number` / `next_page_number`).
- Fix stray `qq` after `{% load tz %}`.

### Tests (`band/tests.py`)

Add a focused test case that creates enough past (and optionally upcoming) gigs to exercise:

- Default `/gigs/` returns at most 10 past gigs and 200
- `/gigs/?page=2` returns the next past-gig slice (correct members / ordering)
- `/gigs/?page=999` (or non-numeric) returns 200 with a valid page

## Out of scope

- Paginating upcoming gigs
- Configurable page size
- Ellipsis / sliding page-number window
- Infinite scroll / HTMX / separate past-gigs URL
