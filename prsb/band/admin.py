from django.contrib import admin
from django.db.models import Value
from django.db.models.functions import Concat

from .models import Song, SongPart, BandMember, Instrument, PartAssignment, GigAttendance, Gig, GigInstrument


class SongPartInline(admin.TabularInline):
    model = SongPart
    extra = 1

class SongAdmin(admin.ModelAdmin):
    inlines = [SongPartInline]
    list_display = ["title", "in_gig_rotation"]

admin.site.register(Song, SongAdmin)


@admin.display(description="Name", ordering=Concat("user__first_name", Value(" "), "user__last_name"))
def band_member_name(obj):
    return obj.user.get_full_name()

@admin.display(description="Is Active?", boolean=True, ordering="user__is_active")
def band_member_active(obj):
    return obj.user.is_active


class BandMemberAdmin(admin.ModelAdmin):
    list_display = [band_member_name, band_member_active]

admin.site.register(BandMember, BandMemberAdmin)

admin.site.register(Instrument)
admin.site.register(PartAssignment)
admin.site.register(Gig)
admin.site.register(GigInstrument)
admin.site.register(GigAttendance)
