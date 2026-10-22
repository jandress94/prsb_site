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
    link = BandSong.objects.filter(song_id=entry.song_id, band=band, is_active=True).first()
    if link is None:
        raise ValidationError("The song must be active in the named band.")
    if not link.in_gig_rotation:
        raise ValidationError("Only and Prefer must name a band that has this song in its gig rotation.")


def song_rotation_member_ids(song_ids, band_ids):
    """Members of gig bands that rotate each song. Songs nobody rotates are omitted."""
    if not song_ids or not band_ids:
        return {}
    song_bands = {}
    rotating_band_ids = set()
    for song_id, band_id in BandSong.objects.filter(
        song_id__in=song_ids,
        band_id__in=band_ids,
        is_active=True,
        in_gig_rotation=True,
    ).values_list("song_id", "band_id"):
        song_bands.setdefault(song_id, set()).add(band_id)
        rotating_band_ids.add(band_id)
    members_by_band = {}
    if rotating_band_ids:
        for band_id, member_id in BandMembership.objects.filter(
            band_id__in=rotating_band_ids,
        ).values_list("band_id", "member_id"):
            members_by_band.setdefault(band_id, set()).add(member_id)
    result = {}
    for song_id, bands in song_bands.items():
        members = set()
        for band_id in bands:
            members.update(members_by_band.get(band_id, ()))
        result[song_id] = members
    return result


def entry_pool_member_ids(entry):
    gig_band_ids = set(entry.gig.bands.values_list("pk", flat=True))
    if len(gig_band_ids) < 2 or entry.staffing_mode in {"", "only"}:
        band_ids = {entry.staffing_band_id} if entry.staffing_band_id else gig_band_ids
    else:
        band_ids = set(gig_band_ids)
    member_ids = set(
        BandMembership.objects.filter(band_id__in=band_ids).values_list("member_id", flat=True)
    )
    if len(gig_band_ids) < 2:
        return member_ids
    # A band that has not rotated the song has not learned its parts.
    know_it = song_rotation_member_ids([entry.song_id], gig_band_ids).get(entry.song_id)
    if know_it is None:
        return member_ids
    return member_ids & know_it
