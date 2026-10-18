from django.db.models import Exists, OuterRef, QuerySet
from django.db.models.functions import Trim
from django.utils import timezone

from .models import Band, BandMember, Gig, GigAttendance

STATUS_NO_STATUS = "no_status"
ALLOWED_STATUSES = frozenset({
    GigAttendance.AVAILABLE,
    GigAttendance.MAYBE_AVAILABLE,
    STATUS_NO_STATUS,
})
STATUS_LABELS = {
    GigAttendance.AVAILABLE: "Available",
    GigAttendance.MAYBE_AVAILABLE: "Maybe available",
    STATUS_NO_STATUS: "No status yet",
}


_MAX_SIGNED_PK = 2**63 - 1


def resolve_gig(raw: str | None, *, band: Band) -> Gig | None:
    if raw is None or raw == "":
        return None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None
    if pk < 1 or pk > _MAX_SIGNED_PK:
        return None
    gig = Gig.objects.filter(pk=pk).first()
    if gig is None or not gig.bands.filter(pk=band.pk).exists():
        return None
    return gig


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


def status_caption_labels(statuses: list[str]) -> list[str]:
    return [STATUS_LABELS[value] for value in statuses if value in STATUS_LABELS]


def _base_members(band: Band) -> QuerySet:
    return (
        BandMember.objects.annotate(_diet=Trim("dietary_restrictions"))
        .filter(
            user__is_active=True,
            bandmembership__band=band,
            bandmembership__is_active=True,
        )
        .exclude(_diet="")
        .order_by("user__first_name", "user__last_name")
        .distinct()
    )


def dietary_restriction_members(
    *,
    gig: Gig | None,
    statuses: list[str] | None,
    band: Band,
) -> QuerySet:
    qs = _base_members(band)
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

    if not clauses:
        return qs.none()

    combined = clauses[0]
    for clause in clauses[1:]:
        combined |= clause
    return qs.filter(combined)


def upcoming_gigs(band: Band) -> QuerySet:
    return (
        Gig.objects.filter(bands=band, end_datetime__gte=timezone.now())
        .order_by("start_datetime")
    )


def past_gigs(band: Band) -> QuerySet:
    return (
        Gig.objects.filter(bands=band, end_datetime__lt=timezone.now())
        .order_by("-start_datetime", "-pk")
    )


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
