from django.contrib import admin
from django.db.models import Value
from django.db.models.functions import Concat
from ordered_model.admin import OrderedModelAdmin


from .models import Song, SongPart, BandMember, Instrument, PartAssignment, GigAttendance, Gig, GigInstrument, \
    GigPartAssignmentOverride


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


class InstrumentAdmin(OrderedModelAdmin):
    list_display = ('name', 'move_up_down_links')

admin.site.register(Instrument, InstrumentAdmin)


class PartAssignmentAdmin(admin.ModelAdmin):
    list_display = ['member', 'song_part__song', 'song_part', 'instrument']
    list_filter = ['member', 'song_part__song', 'song_part', 'instrument']  # TODO: filtering by member doesn't work
admin.site.register(PartAssignment, PartAssignmentAdmin)


admin.site.register(Gig)


class GigInstrumentAdmin(admin.ModelAdmin):
    list_display = ["gig", "instrument"]
    list_filter = ["gig"]
admin.site.register(GigInstrument, GigInstrumentAdmin)


class GigAttendanceAdmin(admin.ModelAdmin):
    list_display = ['gig', 'member', 'status']
    list_filter = ['gig', 'member', 'status']
admin.site.register(GigAttendance, GigAttendanceAdmin)


admin.site.register(GigPartAssignmentOverride)
