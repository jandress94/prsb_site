from dataclasses import dataclass

import django

django.setup()
from band.models import (
    BandMember, Gig, GigAttendance, Instrument, PartAssignment, PerformanceReadiness,
    Song, SongPart,
)
from django.utils import timezone


DEFAULT_LOOKBACK = 10
READY_WEIGHT = 1.0
BACKUP_WEIGHT = 0.5
RISK_EPSILON = 0.01

_READINESS_WEIGHT = {
    PerformanceReadiness.READY: READY_WEIGHT,
    PerformanceReadiness.BACKUP: BACKUP_WEIGHT,
}


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
    coverers: list  # list[CovererRow]; all ready/backup assignments, not_ready omitted


@dataclass(frozen=True)
class SongRiskRow:
    song: Song
    risk: float
    worst_part_name: str
    worst_part_effective_coverage: float
    parts: list  # list[PartRiskRow]; sorted risk desc
    unassigned_members: list  # list[BandMember]; active, no assignments on song


@dataclass(frozen=True)
class CoverageRiskReport:
    lookback_requested: int
    lookback_gigs_used: int  # D
    songs: list  # list[SongRiskRow]; sorted risk desc


def _lookback_gigs(lookback: int) -> list[Gig]:
    now = timezone.now()
    return list(
        Gig.objects.filter(is_small_group=False, start_datetime__lte=now)
        .order_by('-start_datetime')[:lookback]
    )


def _attendance_rates(gigs: list[Gig]) -> dict:
    """Return member_id -> attendance rate (# AVAILABLE / len(gigs)) over the given gigs.

    Members with no AVAILABLE responses among the gigs are absent from the dict;
    use `_get_rate` to resolve a member's rate (handles the D == 0 case too).
    """
    d = len(gigs)
    if d == 0:
        return {}
    gig_ids = [gig.id for gig in gigs]
    counts = {}
    available = GigAttendance.objects.filter(
        gig_id__in=gig_ids, status=GigAttendance.AVAILABLE,
    ).values_list('member_id', flat=True)
    for member_id in available:
        counts[member_id] = counts.get(member_id, 0) + 1
    return {member_id: count / d for member_id, count in counts.items()}


def _get_rate(rates: dict, member_id, d: int) -> float:
    if d == 0:
        return 1.0
    return rates.get(member_id, 0.0)


def _score_part(song_part: SongPart, assignments: list, rates: dict, d: int) -> PartRiskRow:
    coverers = []
    best_contribution_by_member = {}
    for assignment in assignments:
        weight = _READINESS_WEIGHT.get(assignment.performance_readiness)
        if weight is None:
            continue
        rate = _get_rate(rates, assignment.member_id, d)
        contribution = weight * rate
        coverers.append(CovererRow(
            member=assignment.member,
            instrument=assignment.instrument,
            readiness=assignment.performance_readiness,
            attendance_rate=rate,
            contribution=contribution,
        ))
        existing = best_contribution_by_member.get(assignment.member_id)
        if existing is None or contribution > existing:
            best_contribution_by_member[assignment.member_id] = contribution

    effective_coverage = sum(best_contribution_by_member.values())
    risk = 1 / (effective_coverage + RISK_EPSILON)
    return PartRiskRow(
        song_part=song_part,
        effective_coverage=effective_coverage,
        risk=risk,
        coverers=coverers,
    )


def _active_members() -> list:
    return list(
        BandMember.objects.filter(user__is_active=True)
        .select_related('user')
        .order_by('user__first_name', 'user__last_name')
    )


def _unassigned_members(active_members: list, assigned_member_ids: set) -> list:
    return [
        member for member in active_members
        if member.user_id not in assigned_member_ids
    ]


def get_coverage_risk(lookback: int = DEFAULT_LOOKBACK) -> CoverageRiskReport:
    gigs = _lookback_gigs(lookback)
    d = len(gigs)
    rates = _attendance_rates(gigs)

    all_assignments = list(
        PartAssignment.objects.filter(song_part__song__in_gig_rotation=True)
        .exclude(performance_readiness=PerformanceReadiness.NOT_READY)
        .select_related('member__user', 'instrument', 'song_part__song')
    )
    assignments_by_song_part = {}
    for assignment in all_assignments:
        assignments_by_song_part.setdefault(assignment.song_part_id, []).append(assignment)

    assigned_member_ids_by_song = {}
    for assignment in PartAssignment.objects.filter(
        song_part__song__in_gig_rotation=True
    ).values_list('song_part__song_id', 'member_id'):
        song_id, member_id = assignment
        assigned_member_ids_by_song.setdefault(song_id, set()).add(member_id)

    songs = Song.objects.filter(in_gig_rotation=True).prefetch_related('parts')
    active_members = _active_members()

    song_rows = []
    for song in songs:
        parts = list(song.parts.all())
        part_rows = [
            _score_part(part, assignments_by_song_part.get(part.id, []), rates, d)
            for part in parts
        ]
        part_rows.sort(key=lambda p: p.risk, reverse=True)

        if part_rows:
            worst = part_rows[0]
            worst_part_name = worst.song_part.name
            worst_part_effective_coverage = worst.effective_coverage
            song_risk = worst.risk
        else:
            worst_part_name = ""
            worst_part_effective_coverage = 0.0
            song_risk = 1 / RISK_EPSILON

        unassigned = _unassigned_members(active_members, assigned_member_ids_by_song.get(song.id, set()))

        song_rows.append(SongRiskRow(
            song=song,
            risk=song_risk,
            worst_part_name=worst_part_name,
            worst_part_effective_coverage=worst_part_effective_coverage,
            parts=part_rows,
            unassigned_members=unassigned,
        ))

    song_rows.sort(key=lambda s: s.risk, reverse=True)

    return CoverageRiskReport(
        lookback_requested=lookback,
        lookback_gigs_used=d,
        songs=song_rows,
    )
