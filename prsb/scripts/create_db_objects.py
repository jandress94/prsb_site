from typing import Any

import django
from pandas._libs import NaTType

django.setup()

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User

from band.models import BandMember, Song, SongPart, Instrument, PartAssignment
import pandas as pd
import numpy as np

from datetime import timedelta


def get_songs() -> list[Song]:
    songs_df = pd.read_csv('resources/data/Songs.csv').replace({np.nan: ''})
    songs = []

    for _, row in songs_df.iterrows():
        if row.Duration:
            duration_parts = [int(x) for x in row.Duration.split(":")]
            duration = timedelta(minutes=duration_parts[1], seconds=duration_parts[2])
        else:
            duration = None

        songs.append(Song(
            title=row.Title,
            composer=row.Composer,
            duration=duration,
            in_gig_rotation=row.InGigRotation,
            form=row.Form
        ))

    return songs


def get_song_parts(songs: list[Song]) -> list[SongPart]:
    song_parts_df = pd.read_csv('resources/data/SongParts.csv')
    song_parts = []

    title_to_song_map = {s.title: s for s in songs}

    for _, row in song_parts_df.iterrows():
        song_parts.append(SongPart(
            song=title_to_song_map[row['Song Name']],
            name=row['Part Name']
        ))

    return song_parts


def get_instruments() -> list[Instrument]:
    instruments_df = pd.read_csv('resources/data/Instruments.csv')
    instruments = []

    for _, row in instruments_df.iterrows():
        instruments.append(Instrument(
            name=row.Instrument,
            quantity=row.Count
        ))

    return instruments


def get_users() -> list[User]:
    band_members_df = pd.read_csv('resources/data/BandMembers.csv')
    users = []

    for _, row in band_members_df.iterrows():
        if row['Last Name'] == 'Andress':
            continue

        users.append(User(
            username=row.Username,
            first_name=row['First Name'],
            last_name=row['Last Name'],
            email=row.Email,
            password=make_password(row.Password),
            is_staff=row.Staff,
            is_active=row.Active
        ))

    return users


def nan_to_null(x: Any) -> Any:
    if (isinstance(x, float) and np.isnan(x)) or isinstance(x, NaTType):
        return None
    return x


def get_band_members(users: list[User]) -> list[BandMember]:
    band_members_df = pd.read_csv('resources/data/BandMembers.csv', parse_dates=['Birthday'])
    band_members = []

    name_lookup_map = {f'{u.first_name} {u.last_name}': u for u in users}

    for _, row in band_members_df.iterrows():
        user = name_lookup_map[f'{row["First Name"]} {row["Last Name"]}']

        band_members.append(BandMember(
            user=user,
            phone_number=nan_to_null(row.PhoneNumber),
            emergency_contact_name=row.EmergencyContactName,
            emergency_contact_phone=nan_to_null(row.EmergencyContactPhone),
            bio=row.Bio,
            birthday=nan_to_null(row.Birthday),
            dietary_restrictions=row.DietaryRestrictions,
            tshirt_size=row.TShirtSize,
        ))

    return band_members


def get_member_lookup(band_members: list[BandMember]) -> dict[str, BandMember]:
    lookup = {}
    for bm in band_members:
        if bm.user.first_name != 'Ryan':
            lookup[bm.user.first_name] = bm
        else:
            lookup[f'Ryan {bm.user.last_name[0]}'] = bm
    return lookup


def get_song_part_lookup(song_parts: list[SongPart]) -> dict[str, SongPart]:
    return {f'{sp.song.title}.{sp.name}': sp for sp in song_parts}


def do_song_part_lookup(row: pd.Series, song_part_lookup: dict[str, SongPart]) -> SongPart:
    return song_part_lookup[f'{row.Song}.{row.Part}']


def get_instrument_lookup(instruments: list[Instrument]) -> dict[str, Instrument]:
    return {i.name: i for i in instruments}


def get_part_assignments(band_members: list[BandMember], song_parts: list[SongPart], instruments: list[Instrument]) -> list[PartAssignment]:
    part_assignment_df = pd.read_csv('resources/data/PartAssignments.csv')
    part_assignments = []

    user_lookup = get_member_lookup(band_members)
    song_part_lookup = get_song_part_lookup(song_parts)
    instrument_lookup = get_instrument_lookup(instruments)

    for _, row in part_assignment_df.iterrows():
        part_assignments.append(PartAssignment(
            member=user_lookup[row.Person],
            song_part=do_song_part_lookup(row, song_part_lookup),
            instrument=instrument_lookup[row.Instrument]
        ))

    return part_assignments


if __name__ == '__main__':
    songs = Song.objects.all()
    if len(songs) == 0:
        songs = get_songs()
        for song in songs:
            song.save()
    else:
        songs = list(songs)

    song_parts = SongPart.objects.all()
    if len(song_parts) == 0:
        song_parts = get_song_parts(songs)
        for song_part in song_parts:
            song_part.save()
    else:
        song_parts = list(song_parts)

    instruments = Instrument.objects.all()
    if len(instruments) == 0:
        instruments = get_instruments()
        for instrument in instruments:
            instrument.save()
    else:
        instruments = list(instruments)

    users = User.objects.all()
    if len(users) <= 1:
        users = get_users()
        for user in users:
            user.save()
    else:
        users = list(users)

    band_members = BandMember.objects.all()
    if len(band_members) == 0:
        band_members = get_band_members(users)
        for band_member in band_members:
            band_member.save()
    else:
        band_members = list(band_members)

    part_assignments = PartAssignment.objects.all()
    if len(part_assignments) == 0:
        part_assignments = get_part_assignments(band_members, song_parts, instruments)
        for part_assignment in part_assignments:
            part_assignment.save()
    else:
        part_assignments = list(part_assignments)


