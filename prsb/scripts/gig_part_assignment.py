import itertools
from datetime import datetime
from typing import Tuple

import django
import numpy as np
from django.db.models import Exists, OuterRef
from scipy import optimize
from scipy.optimize import LinearConstraint
from collections import Counter

django.setup()
from band.models import Song, SongPart, PartAssignment, Gig, GigAttendance, GigInstrument, BandMember, \
    PerformanceReadiness, GigPartAssignmentOverride, GigSetlistEntry, OverrideType, Instrument, \
    DrumKitCoverPlayer, BandMembership, BandSong
from band.validation import entry_pool_member_ids, song_rotation_member_ids


RAND_SEED = 0


class ScoringConfig:
    SCORE_RANGE = 100
    ASSIGNMENT_WEIGHT_INSTRUMENT = 1
    ASSIGNMENT_PENALTY_PER_SONG = 0.0001
    ASSIGNMENT_WEIGHT_RANDOM = 0.000001

    # Floor for the per-solve penalty that dominates all lower-priority rewards.
    MISSING_PART_PENALTY = SCORE_RANGE
    # A ready soloist must beat even the largest possible instrument-fit reward.
    SOLOIST_REWARD_OVER_MAX_INSTRUMENT = 1

    # Missing-part bands do not overlap: instrument fit can move a score by at most 10 points,
    # while each missing part costs 15 points.
    MISSING_PART_SCORE_PENALTY = 15
    INSTRUMENT_SCORE_RANGE = 10


def get_score(num_missing_parts: int, instrument_fit: float) -> float:
    """Score a song in non-overlapping missing-part bands.

    Instrument fit is the fraction of the inverse-quantity instrument reward earned. It only
    positions a song within its band, so one fewer missing part always produces a higher score.
    """
    fit = min(max(instrument_fit, 0.0), 1.0)
    return (
        ScoringConfig.SCORE_RANGE
        - num_missing_parts * ScoringConfig.MISSING_PART_SCORE_PENALTY
        - (1 - fit) * ScoringConfig.INSTRUMENT_SCORE_RANGE
    )


class GigPartAssignment:
    def __init__(self, song: Song, part_assignments: list[PartAssignment], score: float,
                 attendees: list[BandMember], gig_instruments: list[GigInstrument], setlist_entry=None):
        self.song = song
        self.setlist_entry = setlist_entry
        self.score = score
        self.part_assignments = part_assignments

        playing_attendees = {pa.member for pa in part_assignments}
        self.non_players = [a for a in attendees if a not in playing_attendees]

        covered_parts = {pa.song_part for pa in part_assignments}
        # List, not set: the template renders these in part order.
        self.unplayed_parts = [sp for sp in song.parts.all().order_by('_order') if sp not in covered_parts]

        played_instruments = Counter(pa.instrument for pa in part_assignments)
        remaining_instrument_counts = {gi.instrument: gi.gig_quantity - played_instruments[gi.instrument] for gi in gig_instruments}
        self.unplayed_instruments = {i: c for i, c in remaining_instrument_counts.items() if c != 0}

    @property
    def is_fully_covered(self) -> bool:
        return not self.unplayed_parts


def _partition_overrides(part_assignment_overrides: list[GigPartAssignmentOverride]) -> Tuple[list[GigPartAssignmentOverride], set[tuple]]:
    assign_overrides = []
    not_playing = set()
    for override in part_assignment_overrides:
        if override.override_type == OverrideType.NOT_PLAYING:
            not_playing.add((override.member_id, override.song_part_id, override.gig_instrument.instrument_id))
        else:
            assign_overrides.append(override)
    return assign_overrides, not_playing


def inject_drum_kit_cover_assignments(
    song: Song,
    kit_instrument: Instrument | None,
    available_members: set[BandMember] | set,
    song_overrides: list[GigPartAssignmentOverride],
    cover_players: list,
    kit_repertoire: list[PartAssignment],
) -> list[PartAssignment]:
    if kit_instrument is None or not kit_repertoire:
        return []

    assign_overrides, not_playing = _partition_overrides(song_overrides)
    assign_member_ids = {
        override.member_id
        for override in assign_overrides
        if override.override_type == OverrideType.ASSIGN
    }

    attachment_parts = {pa.song_part for pa in kit_repertoire}
    attachment_part = kit_repertoire[0].song_part
    assert len(attachment_parts) == 1

    kit_instrument_id = kit_instrument.pk
    listed_repertoire_member_ids = {pa.member_id for pa in kit_repertoire}

    listed_available = any(
        pa.member in available_members
        and pa.member_id not in assign_member_ids
        and pa.performance_readiness != PerformanceReadiness.NOT_READY
        and (pa.member_id, pa.song_part_id, kit_instrument_id) not in not_playing
        for pa in kit_repertoire
    )
    if listed_available:
        return []

    eligible = []
    for cover in cover_players:
        member = cover.member
        if member not in available_members:
            continue
        if member.pk in assign_member_ids:
            continue
        if member.pk in listed_repertoire_member_ids:
            continue
        if (member.pk, attachment_part.pk, kit_instrument_id) in not_playing:
            continue
        eligible.append(cover)

    if not eligible:
        return []

    min_priority = min(cover.priority for cover in eligible)
    injected = []
    for cover in eligible:
        if cover.priority != min_priority:
            continue
        assignment = PartAssignment(
            member=cover.member,
            song_part=attachment_part,
            instrument=kit_instrument,
            performance_readiness=PerformanceReadiness.READY,
            can_solo=False,
        )
        assignment.is_drum_kit_cover = True
        injected.append(assignment)
    return injected


def get_gig_song_part_assignments(part_list: list[SongPart], all_assignments: list[PartAssignment],
                                  gig_instruments: list[GigInstrument], member_song_counts: Counter,
                                  part_assignment_overrides: list[GigPartAssignmentOverride],
                                  preferred_member_ids: set[int] | None = None) -> Tuple[list[PartAssignment] | None, float]:
    assign_overrides, not_playing = _partition_overrides(part_assignment_overrides)
    all_assignments = [
        assignment for assignment in all_assignments
        if (assignment.member_id, assignment.song_part_id, assignment.instrument_id) not in not_playing
    ]
    if not all_assignments and not assign_overrides:
        return [], get_score(len(part_list), 0.0)
    members = {}
    instruments = {}
    parts = {}

    instrument_to_count_map = {gi.instrument: gi.gig_quantity for gi in gig_instruments}

    num_vars = 0

    for i, assignment in enumerate(all_assignments):
        members.setdefault(assignment.member, []).append(i)
        instruments.setdefault(assignment.instrument, []).append(i)
        if assignment.performance_readiness == PerformanceReadiness.READY:
            parts.setdefault(assignment.song_part, []).append(i)
    num_assignments = len(all_assignments)
    num_vars += num_assignments

    for i, override in enumerate(assign_overrides, start=num_vars):
        members.setdefault(override.member, []).append(i)
        instruments.setdefault(override.gig_instrument.instrument, []).append(i)
        if override.performance_readiness == PerformanceReadiness.READY:
            parts.setdefault(override.song_part, []).append(i)
    num_overrides = len(assign_overrides)
    num_vars += num_overrides

    num_members = len(members)
    num_instruments = len(instruments)

    num_parts = len(part_list)
    num_vars += num_parts

    ##########################################################################################
    # Constraints
    # There are four types of constraints
    #   1. Member (each person can play only one part)
    #       - all the variables associated with a given member have a coefficient of 1
    #       - lower bound 0 (they might play nothing)
    #       - upper bound 1 (they can play up to one part)
    #   2. Instrument (there are only so many of each instrument)
    #       - all the variables associated with a given instrument have a coefficient of 1
    #       - lower bound 0 (a given instrument type may have no one playing it)
    #       - upper bound is however many of that instrument are at the gig
    #   3. Song Part (detect when no one is assigned to a particular song part)
    #       - all the variables associated with a given song part have a coefficient of 1, as does one part indicator variable
    #       - lower bound 1 (either at least one member plays a song part, or the indicator flips on)
    #       - upper bound infinite (an unlimited number of members can play the part)
    #   4. Overrides (guarantee someone has a particular part)
    #       - the variable for an override has coefficient 1
    #       - lower and upper bound are both 1
    ##########################################################################################
    constraints = []

    # ensure each member only plays once
    coeff_member = np.zeros((num_members, num_vars))
    lb_member = np.zeros(num_members)
    ub_member = np.ones(num_members)
    for i, member_indices in enumerate(members.values()):
        coeff_member[i, member_indices] = 1
    constraints.append(LinearConstraint(coeff_member, lb_member, ub_member))

    # ensure each instrument only played up to the correct number of times
    coeff_instrument = np.zeros((num_instruments, num_vars))
    lb_instrument = np.zeros(num_instruments)
    ub_instrument = np.zeros(num_instruments)
    for i, instrument in enumerate(instruments):
        coeff_instrument[i, instruments[instrument]] = 1
        ub_instrument[i] = instrument_to_count_map[instrument]
    constraints.append(LinearConstraint(coeff_instrument, lb_instrument, ub_instrument))

    # ensure each part is played by at least one (primary / ready) person, or that there's a penalty applied
    coeff_part = np.zeros((num_parts, num_vars))
    lb_part = np.ones(num_parts)
    ub_part = np.inf * np.ones(num_parts)
    for i, part in enumerate(part_list):
        part_indices = parts.get(part, [])
        coeff_part[i, part_indices] = 1
        coeff_part[i, num_assignments + num_overrides + i] = 1
    constraints.append(LinearConstraint(coeff_part, lb_part, ub_part))

    # ensure overrides are respected
    if num_overrides > 0:
        coeff_overrides = np.zeros((num_overrides, num_vars))
        lb_overrides = np.ones(num_overrides)
        ub_overrides = np.ones(num_overrides)
        for i in range(num_overrides):
            coeff_overrides[i, num_assignments + i] = 1
        constraints.append(LinearConstraint(coeff_overrides, lb_overrides, ub_overrides))

    ##########################################################################################
    # Objective Function Coefficients
    # For the variables representing part assignments,
    # the coefficient is -1 / the number that instrument that is coming to the gig.
    # The fewer of an instrument there are, the more we would want to put members there.
    #
    # The last variables catch when no ready member is assigned to a particular part.
    # Their per-solve penalty dominates every solo and instrument reward that could be earned
    # instead, so covering a part always remains the optimizer's first priority.
    ##########################################################################################
    c_instrument = np.zeros(num_vars)
    for instrument, instrument_indices in instruments.items():
        c_instrument[instrument_indices] = (
            -ScoringConfig.SCORE_RANGE
            / 2
            * ScoringConfig.ASSIGNMENT_WEIGHT_INSTRUMENT
            / len(instrument_to_count_map)
            / instrument_to_count_map[instrument]
        )

    c_random = ScoringConfig.ASSIGNMENT_WEIGHT_RANDOM * np.random.random(num_vars)
    c_random[num_assignments:] = 0

    c_per_song_penalty = np.zeros(num_vars)
    for member, member_indices in members.items():
        c_per_song_penalty[member_indices] += ScoringConfig.ASSIGNMENT_PENALTY_PER_SONG * member_song_counts[member]**2

    max_instrument_reward = ScoringConfig.SCORE_RANGE / 2 * ScoringConfig.ASSIGNMENT_WEIGHT_INSTRUMENT
    soloist_reward = (
        max_instrument_reward
        + ScoringConfig.SOLOIST_REWARD_OVER_MAX_INSTRUMENT
    )
    # Nothing outranks covering a part: the penalty must exceed the largest combined
    # solo + instrument reward the solver could earn by leaving one part empty.
    max_concurrent_players = min(num_members, sum(instrument_to_count_map.values()))
    missing_part_penalty = max(
        ScoringConfig.MISSING_PART_PENALTY,
        max_concurrent_players * soloist_reward + max_instrument_reward + 1,
    )

    c_soloist = np.zeros(num_vars)
    for i, assignment in enumerate(all_assignments):
        if (
            assignment.performance_readiness == PerformanceReadiness.READY
            and assignment.can_solo
            and assignment.song_part.has_solo
        ):
            c_soloist[i] = -soloist_reward
    # Override variables (indices num_assignments .. num_assignments+num_overrides-1) stay 0.

    # Reward preferred players by more than one solo plus full instrument fit so they
    # win a contested seat. Other-band players keep their instrument reward, so an open
    # seat is still filled. A hole stays costlier than every preferred reward.
    # A fraction of instrument reward, under the solo margin, keeps a preferred player
    # on the scarcer seat when the other band can fill the open common seat.
    preferred_assignment_reward = soloist_reward + max_instrument_reward + 1
    preferred_instrument_weight = (
        ScoringConfig.SOLOIST_REWARD_OVER_MAX_INSTRUMENT / 2 / max_instrument_reward
    )
    c_preference = np.zeros(num_vars)
    if preferred_member_ids is not None:
        missing_part_penalty = max(
            missing_part_penalty,
            num_assignments * preferred_assignment_reward + 1,
        )
        for index, assignment in enumerate(all_assignments):
            if assignment.member_id in preferred_member_ids:
                c_preference[index] = (
                    -preferred_assignment_reward
                    + c_instrument[index] * preferred_instrument_weight
                )

    c_missing_part_penalty = np.zeros(num_vars)
    c_missing_part_penalty[num_assignments + num_overrides:] = missing_part_penalty

    c = c_instrument + c_random + c_per_song_penalty + c_soloist + c_preference + c_missing_part_penalty

    ##########################################################################################
    # Other Settings
    # all variables are binary (integers from 0 to 1 inclusive)
    ##########################################################################################
    bounds = optimize.Bounds(0, 1)
    integrality = np.ones_like(c)

    ##########################################################################################
    # Solve
    ##########################################################################################
    result = optimize.milp(c=c, constraints=constraints, bounds=bounds, integrality=integrality)

    if not result.success:
        return None, -1

    ##########################################################################################
    # Process Results
    ##########################################################################################
    overrides_as_part_assignments = [PartAssignment(member=pao.member,
                                                    song_part=pao.song_part,
                                                    instrument=pao.gig_instrument.instrument,
                                                    performance_readiness=pao.performance_readiness)
                                     for pao in assign_overrides]
    gig_part_assignments = list(itertools.compress(all_assignments + overrides_as_part_assignments, result.x))
    gig_part_assignments = sorted(
        gig_part_assignments,
        key=lambda gpa: (gpa.song_part._order, gpa.instrument.order, gpa.member.user.get_full_name()),
    )

    # c_instrument is negative and reaches this magnitude when every instrument slot is filled.
    max_instrument_reward = ScoringConfig.SCORE_RANGE / 2 * ScoringConfig.ASSIGNMENT_WEIGHT_INSTRUMENT
    instrument_fit = -sum(itertools.compress(c_instrument, result.x)) / max_instrument_reward

    covered_parts = {pa.song_part for pa in gig_part_assignments}
    num_missing_parts = sum(1 for part in part_list if part not in covered_parts)

    return gig_part_assignments, get_score(num_missing_parts, instrument_fit)


def pool_rank(entry):
    if entry is None:
        return 3
    if entry.gig.bands.count() < 2 or entry.staffing_mode in {"", "only"}:
        return 0 if entry.gig.bands.count() < 2 else 1
    return 2


def _preferred_member_ids(entry):
    if entry is None or entry.staffing_mode != "prefer":
        return None
    return set(
        BandMembership.objects.filter(band_id=entry.staffing_band_id).values_list("member_id", flat=True)
    )


def _cover_players_for(entry, gig_bands, cover_rows):
    """Only and one-band gigs use one band's cover list. Prefer, Any, and recommendations use every gig band."""
    if entry is not None and (len(gig_bands) < 2 or entry.staffing_mode in {"", "only"}):
        if len(gig_bands) < 2 or not entry.staffing_band_id:
            band_ids = {band.pk for band in gig_bands}
        else:
            band_ids = {entry.staffing_band_id}
    else:
        band_ids = {band.pk for band in gig_bands}
    chosen = {}
    for cover in cover_rows:
        if cover.band_id not in band_ids:
            continue
        current = chosen.get(cover.member_id)
        rank = (cover.priority, cover.band.name)
        if current is None or rank < (current.priority, current.band.name):
            chosen[cover.member_id] = cover
    return list(chosen.values())


def get_gig_part_assignments(gig: Gig, part_assignment_overrides: list[GigPartAssignmentOverride]) -> Tuple[list[GigPartAssignment], list[GigPartAssignment], Counter]:
    np.random.seed(RAND_SEED)
    attendees = list(
        BandMember.objects.filter(
            gigattendance__gig=gig, gigattendance__status=GigAttendance.AVAILABLE,
        ).select_related("user")
    )
    available_by_id = {member.pk: member for member in attendees}
    gig_instruments: list[GigInstrument] = list(GigInstrument.objects.filter(gig=gig))
    gig_bands = list(gig.bands.all())

    gig_part_assignments_setlist = []
    gig_part_assignments_recs = []
    member_song_counts = Counter()

    entries = list(
        GigSetlistEntry.objects.filter(gig=gig, song__isnull=False)
        .select_related("song", "staffing_band", "gig")
        .prefetch_related("song__parts")
        .order_by("_order")
    )
    setlist_song_ids = {entry.song_id for entry in entries}
    rec_song_ids = set(
        BandSong.objects.filter(
            band__in=gig_bands,
            is_active=True,
            in_gig_rotation=True,
        ).exclude(song_id__in=setlist_song_ids).values_list("song_id", flat=True)
    )
    relevant_song_ids = setlist_song_ids | rec_song_ids

    part_rows_by_song = {}
    if relevant_song_ids and gig_instruments:
        part_rows = PartAssignment.objects.filter(
            ~Exists(GigPartAssignmentOverride.objects.filter(
                gig_instrument__gig=gig,
                song_part__song=OuterRef("song_part__song"),
                member=OuterRef("member"),
                override_type=OverrideType.ASSIGN,
            )),
            member_id__in=available_by_id,
            instrument__giginstrument__in=gig_instruments,
            song_part__song_id__in=relevant_song_ids,
        ).exclude(
            performance_readiness=PerformanceReadiness.NOT_READY,
        ).select_related("member__user", "song_part", "instrument")
        for row in part_rows:
            part_rows_by_song.setdefault(row.song_part.song_id, []).append(row)

    overrides_by_song = {}
    for override in part_assignment_overrides:
        overrides_by_song.setdefault(override.song_part.song_id, []).append(override)

    kit_instrument = (
        Instrument.objects.filter(is_drum_kit_cover_instrument=True).order_by("order").first()
    )
    gig_has_kit = kit_instrument is not None and any(
        gi.instrument_id == kit_instrument.id for gi in gig_instruments
    )
    cover_rows = list(
        DrumKitCoverPlayer.objects.filter(band__in=gig_bands).select_related("member__user", "band")
    ) if gig_has_kit else []
    kit_repertoire_by_song = {}
    if gig_has_kit and relevant_song_ids:
        kit_pas = PartAssignment.objects.filter(
            instrument=kit_instrument,
            song_part__song_id__in=relevant_song_ids,
        ).select_related("member", "song_part", "instrument").order_by(
            "song_part___order", "pk",
        )
        for pa in kit_pas:
            kit_repertoire_by_song.setdefault(pa.song_part.song_id, []).append(pa)

    rec_pool = set(
        BandMembership.objects.filter(band__in=gig_bands).values_list("member_id", flat=True)
    ) & set(available_by_id)
    rotation_members = (
        song_rotation_member_ids(rec_song_ids, {band.pk for band in gig_bands})
        if len(gig_bands) >= 2
        else {}
    )
    rec_songs = {
        song.pk: song
        for song in Song.objects.filter(pk__in=rec_song_ids).prefetch_related("parts")
    }

    def pool_members(member_ids):
        return {available_by_id[member_id] for member_id in member_ids if member_id in available_by_id}

    work = []
    for entry in entries:
        pool = entry_pool_member_ids(entry)
        rows = [row for row in part_rows_by_song.get(entry.song_id, []) if row.member_id in pool]
        rows.sort(key=lambda row: row.pk)
        work.append((entry, entry.song, rows, pool_members(pool)))
    for song_id, song in rec_songs.items():
        allowed = rec_pool & rotation_members[song_id] if song_id in rotation_members else rec_pool
        rows = [row for row in part_rows_by_song.get(song_id, []) if row.member_id in allowed]
        rows.sort(key=lambda row: row.pk)
        if not rows and song_id not in kit_repertoire_by_song and song_id not in overrides_by_song:
            continue
        work.append((None, song, rows, pool_members(allowed)))

    work.sort(key=lambda item: (
        pool_rank(item[0]),
        len(item[2]),
        item[1].title,
        item[0]._order if item[0] else 0,
    ))

    song_counts_to_return = None
    for entry, song, rows, available_members in work:
        song_overrides = overrides_by_song.get(song.id, [])
        candidates = list(rows)
        if gig_has_kit:
            candidates.extend(inject_drum_kit_cover_assignments(
                song=song,
                kit_instrument=kit_instrument,
                available_members=available_members,
                song_overrides=song_overrides,
                cover_players=_cover_players_for(entry, gig_bands, cover_rows),
                kit_repertoire=kit_repertoire_by_song.get(song.id, []),
            ))
        if entry is None and not candidates and not song_overrides:
            continue

        part_assignments, score = get_gig_song_part_assignments(
            list(song.parts.all()),
            candidates,
            gig_instruments,
            member_song_counts,
            song_overrides,
            _preferred_member_ids(entry),
        )
        if part_assignments is None:
            continue

        member_song_counts.update([
            pa.member for pa in part_assignments if pa.instrument.include_in_gig_song_count
        ])
        result = GigPartAssignment(
            song=song,
            part_assignments=part_assignments,
            score=score,
            attendees=[member for member in attendees if member in available_members],
            gig_instruments=gig_instruments,
            setlist_entry=entry,
        )
        if entry is not None:
            gig_part_assignments_setlist.append(result)
            song_counts_to_return = member_song_counts.copy()
        else:
            gig_part_assignments_recs.append(result)

    if song_counts_to_return is None:
        song_counts_to_return = member_song_counts

    gig_part_assignments_setlist = sorted(
        gig_part_assignments_setlist,
        key=lambda x: (x.song.title, x.setlist_entry._order if x.setlist_entry else 0),
    )
    gig_part_assignments_recs = sorted(gig_part_assignments_recs, key=lambda x: (-x.score, x.song.title))

    return gig_part_assignments_setlist, gig_part_assignments_recs, song_counts_to_return


def get_max_instrument_usage(
    gig_part_assignments: list[GigPartAssignment],
    gig_instruments: list[GigInstrument],
) -> list[Tuple]:
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


def main():
    gig = Gig.objects.get(name__contains="Wedding")
    print(gig)

    gig.gigattendance_set.filter(status=GigAttendance.AVAILABLE)

    print(get_gig_part_assignments(gig))



if __name__ == '__main__':
    main()
