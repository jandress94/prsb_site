from django.contrib import admin
from django.db.models import Value, Exists, OuterRef
from django.db.models.functions import Concat
from django.utils import timezone
from ordered_model.admin import OrderedModelAdmin
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


from .models import Song, SongPart, BandMember, Instrument, PartAssignment, GigAttendance, Gig, GigInstrument, \
    GigPartAssignmentOverride, GigSetlistEntry, BandSpecialDate


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


class MemberUserFilter(admin.SimpleListFilter):
    title = _('Member')  # Title displayed in the admin filter sidebar
    parameter_name = 'member_user'  # URL parameter used in the querystring

    def lookups(self, request, model_admin):
        """
        Returns a list of tuples.
        The first element in each tuple is the key for filtering,
        and the second element is the display name in the filter.
        """
        return [(user.id, user.get_full_name()) for user in User.objects.all().order_by('first_name', 'last_name')]

    def queryset(self, request, queryset):
        """
        Returns the filtered queryset based on the selected user.
        """
        if self.value():  # If a filter value (User id) was selected
            return queryset.filter(member__user__id=self.value())
        return queryset


class PartAssignmentAdmin(admin.ModelAdmin):
    @admin.display(description='Song', ordering='song_part__song')
    def song_title(self, obj: PartAssignment):
        return obj.song_part.song

    list_display = ['member', 'song_title', 'song_part', 'instrument']
    list_filter = [MemberUserFilter, 'song_part__song', 'song_part', 'instrument']
admin.site.register(PartAssignment, PartAssignmentAdmin)


@admin.display(description="Upcoming?", boolean=True)
def gig_is_upcoming(obj: Gig):
    return obj.start_datetime >= timezone.now()

@admin.display(description="Date")
def gig_date(obj: Gig):
    return obj.start_datetime.date()


class GigAdmin(admin.ModelAdmin):
    list_display = ['name', gig_is_upcoming, gig_date]
    list_filter = ['start_datetime']

admin.site.register(Gig, GigAdmin)


@admin.display(description="Is Break?", boolean=True)
def setlist_entry_is_break(obj: GigSetlistEntry):
    return obj.break_duration is not None

class GigSetlistEntryAdmin(admin.ModelAdmin):
    list_display = ['gig', 'song', setlist_entry_is_break]
    list_filter = ['gig', ]
admin.site.register(GigSetlistEntry, GigSetlistEntryAdmin)


class GigInstrumentAdmin(admin.ModelAdmin):
    list_display = ["gig", "instrument"]
    list_filter = ["gig"]
admin.site.register(GigInstrument, GigInstrumentAdmin)


class GigAttendanceAdmin(admin.ModelAdmin):
    list_display = ['gig', 'member', 'status']
    list_filter = ['gig', MemberUserFilter, 'status']
admin.site.register(GigAttendance, GigAttendanceAdmin)


class GigFilter(admin.SimpleListFilter):
    title = _('Gig')  # Title displayed in the admin filter sidebar
    parameter_name = 'gig'  # URL parameter used in the querystring

    def lookups(self, request, model_admin):
        """
        Returns a list of tuples.
        The first element in each tuple is the key for filtering,
        and the second element is the display name in the filter.
        """
        return [(gig.id, gig.name) for gig in Gig.objects.filter(Exists(GigPartAssignmentOverride.objects.filter(gig_instrument__gig=OuterRef('pk'))))]

    def queryset(self, request, queryset):
        """
        Returns the filtered queryset based on the selected gig.
        """
        if self.value():  # If a filter value (User id) was selected
            return queryset.filter(gig_instrument__gig=self.value())
        return queryset


class SongFilter(admin.SimpleListFilter):
    title = _('Song')  # Title displayed in the admin filter sidebar
    parameter_name = 'song'  # URL parameter used in the querystring

    def lookups(self, request, model_admin):
        """
        Returns a list of tuples.
        The first element in each tuple is the key for filtering,
        and the second element is the display name in the filter.
        """
        return [(song.id, song.title) for song in Song.objects.filter(Exists(GigPartAssignmentOverride.objects.filter(song_part__song=OuterRef('pk'))))]

    def queryset(self, request, queryset):
        """
        Returns the filtered queryset based on the selected gig.
        """
        if self.value():  # If a filter value (User id) was selected
            return queryset.filter(song_part__song=self.value())
        return queryset


class GigPartAssignmentOverrideAdmin(admin.ModelAdmin):
    @admin.display(description='Gig', ordering='gig_instrument__gig')
    def gig(self, obj: GigPartAssignmentOverride):
        return obj.gig_instrument.gig

    @admin.display(description='Song', ordering='song_part__song')
    def song_title(self, obj: GigPartAssignmentOverride):
        return obj.song_part.song

    @admin.display(description='Instrument', ordering='gig_instrument')
    def instrument(self, obj: GigPartAssignmentOverride):
        return obj.gig_instrument.instrument

    list_display = ['gig', 'member', 'song_title', 'song_part', 'instrument']
    list_filter = [GigFilter, MemberUserFilter, SongFilter]

admin.site.register(GigPartAssignmentOverride, GigPartAssignmentOverrideAdmin)


admin.site.register(BandSpecialDate)
