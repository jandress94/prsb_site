from .models import BandMembership, BandSong


def link_member(band, member):
    row, _ = BandMembership.objects.get_or_create(band=band, member=member)
    if not row.is_active:
        row.is_active = True
        row.save(update_fields=["is_active"])
    return row


def link_song(band, song, in_gig_rotation=False):
    row, created = BandSong.objects.get_or_create(
        band=band, song=song, defaults={"in_gig_rotation": in_gig_rotation, "is_active": True},
    )
    if not created and not row.is_active:
        row.is_active = True
        row.in_gig_rotation = in_gig_rotation
        row.save(update_fields=["is_active", "in_gig_rotation"])
    return row
