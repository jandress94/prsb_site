from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Band, Gig


def active_bands():
    return Band.objects.filter(is_active=True)


def get_band(slug):
    return get_object_or_404(Band, slug=slug)


def get_gig_for_band(gig_id, band):
    gig = get_object_or_404(Gig, id=gig_id)
    if not gig.bands.filter(pk=band.pk).exists():
        raise Http404
    return gig
