import itertools
from typing import Tuple, NamedTuple

import django
import numpy as np
from scipy import optimize
from scipy.optimize import LinearConstraint

django.setup()
from band.models import BandMember, Song, SongPart, Instrument, PartAssignment, Gig, GigAttendance, GigInstrument

from itertools import groupby

MISSING_PART_PENALTY = 10


class GigPartAssignment(NamedTuple):
    song: Song
    part_assignments: list[PartAssignment]
    score: float


def get_gig_song_part_assignments(part_list: list[SongPart], part_assignment_map: dict[SongPart, list[PartAssignment]], gig_instruments: list[GigInstrument]) -> Tuple[list[PartAssignment, float]]:
    all_assignments = list(itertools.chain(*(part_assignment_map.get(part, []) for part in part_list)))

    members = {}
    instruments = {}
    parts = {}

    instrument_to_count_map = {gi.instrument: gi.gig_quantity for gi in gig_instruments}

    for i, assignment in enumerate(all_assignments):
        members.setdefault(assignment.member, []).append(i)
        instruments.setdefault(assignment.instrument, []).append(i)
        parts.setdefault(assignment.song_part, []).append(i)

    num_song_parts = len(part_list)
    num_members = len(members)
    num_instruments = len(instruments)
    num_parts = len(parts)
    num_assignments = len(all_assignments)
    num_vars = num_assignments + num_parts  # one var per assignment with an extra var to represent missing a part
    missing_part_penalty = MISSING_PART_PENALTY / num_song_parts

    ##########################################################################################
    # Constraints
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

    # ensure each part is played by at least one person, or that there's a penalty applied
    coeff_part = np.zeros((num_parts, num_vars))
    lb_part = np.ones(num_parts)
    ub_part = np.inf * np.ones(num_parts)
    for i, part_indices in enumerate(parts.values()):
        coeff_part[i, part_indices] = 1
        coeff_part[i, num_assignments + i] = 1
    constraints.append(LinearConstraint(coeff_part, lb_part, ub_part))

    ##########################################################################################
    # Objective Function Coefficients
    ##########################################################################################
    c = np.ones(num_vars) * missing_part_penalty
    for instrument, instrument_indices in instruments.items():
        c[instrument_indices] = -1. / instrument_to_count_map[instrument]

    ##########################################################################################
    # Other Settings
    ##########################################################################################
    bounds = optimize.Bounds(0, 1)
    integrality = np.ones_like(c)

    ##########################################################################################
    # Solve
    ##########################################################################################
    result = optimize.milp(c=c, constraints=constraints, bounds=bounds, integrality=integrality)

    ##########################################################################################
    # Process Results
    ##########################################################################################
    gig_part_assignments = list(itertools.compress(all_assignments, result.x))

    score = result.fun
    score += (num_song_parts - len({gpa.song_part for gpa in gig_part_assignments})) * missing_part_penalty

    return gig_part_assignments, score


def get_gig_part_assignments(gig: Gig):
    gig_instruments = list(GigInstrument.objects.filter(gig=gig))

    attendee_part_assignments = PartAssignment.objects.filter(member__gigattendance__gig=gig,
                                                              member__gigattendance__status=GigAttendance.AVAILABLE,
                                                              instrument__giginstrument__gig=gig,
                                                              song_part__song__in_gig_rotation=True).order_by('song_part__song__title', 'song_part___order')

    parts = SongPart.objects.filter(song__in_gig_rotation=True).order_by('song__title', '_order')
    song_to_parts_map = {song: list(parts_it) for song, parts_it in groupby(parts, lambda sp: sp.song)}
    part_assignment_map = {part: list(assignments) for part, assignments in groupby(attendee_part_assignments, lambda pa: pa.song_part)}

    gig_part_assignments = []
    for song, part_list in song_to_parts_map.items():
        part_assignments, score = get_gig_song_part_assignments(part_list, part_assignment_map, gig_instruments)
        gig_part_assignments.append(GigPartAssignment(song=song, part_assignments=part_assignments, score=score))

    return sorted(gig_part_assignments, key=lambda x: (x.score, x.song.title))


def main():
    gig = Gig.objects.get(name__contains="Wedding")
    print(gig)

    gig.gigattendance_set.filter(status=GigAttendance.AVAILABLE)

    print(get_gig_part_assignments(gig))



if __name__ == '__main__':
    main()
