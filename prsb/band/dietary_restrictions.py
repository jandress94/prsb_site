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


_MAX_SIGNED_PK = 2**63 - 1


def resolve_gig(raw: str | None) -> Gig | None:
    if raw is None or raw == "":
        return None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None
    if pk < 1 or pk > _MAX_SIGNED_PK:
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

    if not clauses:
        return qs.none()

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
