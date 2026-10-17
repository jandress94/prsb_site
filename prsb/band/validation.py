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
    if not BandSong.objects.filter(song_id=entry.song_id, band=band, is_active=True).exists():
        raise ValidationError("The song must be active in the named band.")


def entry_pool_member_ids(entry):
    gig_band_ids = set(entry.gig.bands.values_list("pk", flat=True))
    if entry.gig.bands.count() < 2 or entry.staffing_mode in {"", "only"}:
        band_ids = {entry.staffing_band_id} if entry.staffing_band_id else gig_band_ids
    elif entry.staffing_mode == "prefer":
        band_ids = gig_band_ids
    else:
        band_ids = gig_band_ids
    return set(BandMembership.objects.filter(band_id__in=band_ids).values_list("member_id", flat=True))
