import itertools
from datetime import datetime
from itertools import groupby
from typing import Tuple

import django
import numpy as np
from django.db.models import Exists, OuterRef
from scipy import optimize
from scipy.optimize import LinearConstraint
from collections import Counter

django.setup()
from band.models import Song, SongPart, PartAssignment, Gig, GigAttendance, GigInstrument, BandMember, \
    PerformanceReadiness, GigPartAssignmentOverride


class ScoringConfig:
    SCORE_RANGE = 100
    ASSIGNMENT_WEIGHT_INSTRUMENT = 1
    ASSIGNMENT_PENALTY_PER_SONG = 0.0001
    ASSIGNMENT_WEIGHT_RANDOM = 0.000001


class GigPartAssignment:
    def __init__(self, song: Song, part_assignments: list[PartAssignment], score: float,
                 attendees: list[BandMember], gig_instruments: list[GigInstrument]):
        self.song = song
        self.score = score
        self.part_assignments = part_assignments

        playing_attendees = {pa.member for pa in part_assignments}
        self.non_players = [a for a in attendees if a not in playing_attendees]

        covered_parts = {pa.song_part for pa in part_assignments}
        self.unplayed_parts = {sp for sp in song.parts.all() if sp not in covered_parts}

        played_instruments = Counter(pa.instrument for pa in part_assignments)
        remaining_instrument_counts = {gi.instrument: gi.gig_quantity - played_instruments[gi.instrument] for gi in gig_instruments}
        self.unplayed_instruments = {i: c for i, c in remaining_instrument_counts.items() if c != 0}


def get_gig_song_part_assignments(part_list: list[SongPart], all_assignments: list[PartAssignment],
                                  gig_instruments: list[GigInstrument], member_song_counts: Counter,
                                  part_assignment_overrides: list[GigPartAssignmentOverride]) -> Tuple[list[PartAssignment] | None, float]:
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

    for i, override in enumerate(part_assignment_overrides, start=num_vars):
        members.setdefault(override.member, []).append(i)
        instruments.setdefault(override.gig_instrument.instrument, []).append(i)
        if override.performance_readiness == PerformanceReadiness.READY:
            parts.setdefault(override.song_part, []).append(i)
    num_overrides = len(part_assignment_overrides)
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
    # The last variables are there to catch when no one is assigned to a particular part.
    # When we use one of these variables, the coefficient is MISSING_PART_PENALTY / num_parts.
    ##########################################################################################
    c_instrument = np.zeros(num_vars)
    for instrument, instrument_indices in instruments.items():
        c_instrument[instrument_indices] = -ScoringConfig.SCORE_RANGE / 2 / len(instrument_to_count_map) / instrument_to_count_map[instrument]

    c_random = ScoringConfig.ASSIGNMENT_WEIGHT_RANDOM * np.random.random(num_vars)
    c_random[num_assignments:] = 0

    c_per_song_penalty = np.zeros(num_vars)
    for member, member_indices in members.items():
        c_per_song_penalty[member_indices] += ScoringConfig.ASSIGNMENT_PENALTY_PER_SONG * member_song_counts[member]**2

    c_missing_part_penalty = np.zeros(num_vars)
    c_missing_part_penalty[-num_parts:] = ScoringConfig.SCORE_RANGE / num_parts / 2

    c = c_instrument + c_random + c_per_song_penalty + c_missing_part_penalty

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
                                     for pao in part_assignment_overrides]
    gig_part_assignments = list(itertools.compress(all_assignments + overrides_as_part_assignments, result.x))
    gig_part_assignments = sorted(gig_part_assignments, key=lambda gpa: (gpa.song_part._order, gpa.member.user.get_full_name()))

    score = sum(itertools.compress(c_instrument + c_missing_part_penalty, result.x))
    score = ScoringConfig.SCORE_RANGE / 2 - score

    return gig_part_assignments, score


def get_gig_part_assignments(gig: Gig, part_assignment_overrides: list[GigPartAssignmentOverride]) -> Tuple[list[GigPartAssignment], Counter]:
    attendees = BandMember.objects.filter(gigattendance__gig=gig, gigattendance__status=GigAttendance.AVAILABLE)
    gig_instruments: list[GigInstrument] = list(GigInstrument.objects.filter(gig=gig))

    gig_part_assignments = []
    member_song_counts = Counter()

    all_part_assignments = PartAssignment.objects.filter(~Exists(GigPartAssignmentOverride.objects.filter(gig_instrument__gig=gig,
                                                                                                          song_part__song=OuterRef("song_part__song"),
                                                                                                          member=OuterRef("member"))),
                                                         member__gigattendance__gig=gig,
                                                         member__gigattendance__status=GigAttendance.AVAILABLE,
                                                         instrument__giginstrument__in=gig_instruments,
                                                         song_part__song__in_gig_rotation=True) \
        .exclude(performance_readiness=PerformanceReadiness.NOT_READY) \
        .order_by("song_part__song")

    song_to_part_assignments = {s: list(p) for s, p in groupby(all_part_assignments, lambda pa: pa.song_part.song)}
    song_to_overrides = {s: list(o) for s, o in groupby(part_assignment_overrides, lambda pao: pao.song_part.song)}

    for song, attendee_part_assignments in sorted(song_to_part_assignments.items(), key=lambda s_apa: len(s_apa[1])):
        part_list = list(song.parts.all())

        part_assignments, score = get_gig_song_part_assignments(part_list, list(attendee_part_assignments), gig_instruments, member_song_counts, song_to_overrides.get(song, []))
        if part_assignments is None:
            # invalid constraints
            continue

        gig_part_assignments.append(GigPartAssignment(song=song, part_assignments=part_assignments, score=score, attendees=attendees, gig_instruments=gig_instruments))

        member_song_counts.update([pa.member for pa in part_assignments])

    return sorted(gig_part_assignments, key=lambda x: (-x.score, x.song.title)), member_song_counts


def main():
    gig = Gig.objects.get(name__contains="Wedding")
    print(gig)

    gig.gigattendance_set.filter(status=GigAttendance.AVAILABLE)

    print(get_gig_part_assignments(gig))



if __name__ == '__main__':
    main()
