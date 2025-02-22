from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Exists, OuterRef
from django import forms
from django.db import connection
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from scripts.gig_part_assignment import get_gig_part_assignments
from .models import Song, Gig, GigAttendance, BandMember, PartAssignment, Instrument, SongPart, \
    GigPartAssignmentOverride, GigInstrument, GigSetlistEntry


def index(request):
    return render(request, "band/index.html")


class MemberListView(generic.ListView):
    def get_queryset(self):
        return BandMember.objects.filter(user__is_active=True).order_by('user__first_name', 'user__last_name')


def get_missing_songs_for_member(member: BandMember):
    return Song.objects.filter(~Exists(PartAssignment.objects.filter(member=member, song_part__song=OuterRef("pk"))))


class MemberDetailView(generic.DetailView):
    model = BandMember

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        current_member = context['bandmember']

        context["part_assignments"] = PartAssignment.objects.filter(
            song_part__song__in_gig_rotation=True,
            member=current_member
        ).order_by('song_part__song__title', 'song_part___order', 'instrument__name')

        context["missing_songs"] = get_missing_songs_for_member(current_member)

        context["upcoming_gig_attendance"] = GigAttendance.objects.filter(
            member=current_member,
            gig__start_datetime__gte=timezone.now()
        )
        return context


# class MemberUpdateView(generic.UpdateView):
#     model = BandMember
#     template_name_suffix = '_update_form'
#     fields = [#'user__email',
#               'phone_number',
#               'emergency_contact_name',
#               'emergency_contact_phone']


class SongListView(generic.ListView):
    model = Song


class SongDetailView(generic.DetailView):
    model = Song

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        song = context['song']

        context["part_assignments"] = part_assignments = PartAssignment.objects.filter(
            song_part__song=song
        ).order_by('song_part___order', 'instrument__name', 'member__user__first_name', 'member__user__last_name')

        all_parts = SongPart.objects.filter(song=song)
        context['missing_parts'] = [p for p in all_parts if p not in {pa.song_part for pa in part_assignments}]

        all_members = BandMember.objects.filter(user__is_active=True)
        context['missing_members'] = [m for m in all_members if m not in {pa.member for pa in part_assignments}]

        return context


class SongUpdateView(generic.UpdateView):
    model = Song
    template_name_suffix = '_update_form'
    fields = ['title',
              'composer',
              'duration',
              'in_gig_rotation',
              'form']


class SongCreateView(generic.CreateView):
    model = Song
    template_name_suffix = '_create_form'
    fields = ['title',
              'composer',
              'duration',
              'in_gig_rotation',
              'form']


def get_missing_person_songs():
    with connection.cursor() as cursor:
        query = f"""
            WITH missing_ids AS (
                SELECT
                    b.user_id,
                    s.title
                FROM
                    {BandMember._meta.db_table} b,
                    {Song._meta.db_table} s
                WHERE
                    NOT EXISTS (
                        SELECT 
                            1
                        FROM 
                            {PartAssignment._meta.db_table} p
                        INNER JOIN 
                            {SongPart._meta.db_table} sp
                        ON
                            p.song_part_id = sp.id
                        WHERE
                            p.member_id = b.user_id
                            AND sp.song_id = s.id
                    )
                )
                SELECT
                    u.first_name || ' ' || u.last_name AS name,
                    m.title
                FROM
                    missing_ids m
                INNER JOIN
                    {User._meta.db_table} u
                ON
                    m.user_id = u.id
                WHERE
                    u.is_active
                ORDER BY
                    title, name
        """
        cursor.execute(query)

        return cursor.fetchall()


class PartAssignmentListView(generic.ListView):
    model = PartAssignment
    ordering = ['song_part__song__title',
                'song_part___order',
                'instrument__name',
                'member__user__first_name',
                'member__user__last_name']

    def get_queryset(self):
        return PartAssignment.objects.filter(member__user__is_active=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['missing_person_songs'] = get_missing_person_songs()
        return context

class PartAssignmentForm(forms.ModelForm):
    class Meta:
        model = PartAssignment
        fields = ['member', 'song_part', 'instrument', 'performance_readiness']

    def __init__(self, *args, **kwargs):
        member_id = kwargs.pop('member_id', None)
        song_id = kwargs.pop('song_id', None)
        super().__init__(*args, **kwargs)

        if member_id is not None:
            self.fields['member'].initial = BandMember.objects.get(pk=member_id)
            self.fields['member'].queryset = BandMember.objects.filter(pk=member_id)
        else:
            self.fields['member'].queryset = BandMember.objects.filter(user__is_active=True)

        if song_id is not None:
            self.fields['song_part'].queryset = SongPart.objects.filter(song_id=song_id).order_by('song', '_order')
        else:
            self.fields['song_part'].queryset = SongPart.objects.order_by('song', '_order')


class MemberPartAssignmentCreateView(generic.CreateView):
    model = PartAssignment
    form_class = PartAssignmentForm
    template_name_suffix = '_create_form'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['member_id'] = self.kwargs['member_id']
        return kwargs

    def get_success_url(self):
        return reverse("band:member_detail", kwargs={"pk": self.kwargs['member_id']})


class MemberPartAssignmentUpdateView(generic.UpdateView):
    model = PartAssignment
    form_class = PartAssignmentForm
    template_name_suffix = '_update_form'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['member_id'] = self.kwargs['member_id']
        return kwargs

    def get_success_url(self):
        return reverse("band:member_detail", kwargs={"pk": self.kwargs['member_id']})


class SongPartAssignmentCreateView(generic.CreateView):
    model = PartAssignment
    form_class = PartAssignmentForm
    template_name_suffix = '_create_form'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['song_id'] = self.kwargs['song_id']
        return kwargs

    def get_success_url(self):
        return reverse("band:song_detail", kwargs={"pk": self.kwargs['song_id']})


class SongPartAssignmentUpdateView(generic.UpdateView):
    model = PartAssignment
    form_class = PartAssignmentForm
    template_name_suffix = '_update_form'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['song_id'] = self.kwargs['song_id']
        return kwargs

    def get_success_url(self):
        return reverse("band:song_detail", kwargs={"pk": self.kwargs['song_id']})


class GigListView(generic.ListView):
    model = Gig

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['upcoming_gigs'] = Gig.objects.filter(start_datetime__gte=timezone.now()).order_by('start_datetime')
        context['past_gigs'] = Gig.objects.filter(start_datetime__lt=timezone.now())
        return context


class GigDetailView(generic.DetailView):
    model = Gig

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gig = context['object']

        set_list = GigSetlistEntry.objects.filter(gig=gig)
        context['setlist'] = set_list

        if len(set_list) > 0:
            music_duration = timedelta()
            total_duration = timedelta()

            for set_list_entry in set_list:
                if set_list_entry.song is None:
                    total_duration += set_list_entry.break_duration
                elif set_list_entry.song.duration is not None:
                    music_duration += set_list_entry.song.duration

            context['music_duration'] = music_duration
            total_duration += music_duration
            if total_duration != music_duration:
                context['total_duration'] = total_duration


        context['part_assignment_overrides'] = GigPartAssignmentOverride.objects.filter(gig_instrument__gig=gig).order_by('song_part__song', 'song_part', 'member')

        context["gig_part_assignments"], member_song_counts = get_gig_part_assignments(gig, context['part_assignment_overrides'])
        context['member_song_counts'] = sorted([(k, v) for k, v in member_song_counts.items()], key=lambda x: x[1], reverse=True)

        for availability in GigAttendance.AVAILABILITY_CHOICES:
            members = gig.gigattendance_set.filter(status=availability)
            context[f"{availability}_members"] = sorted(members, key=lambda ga: ga.member.user.get_full_name())

        context["missing_members"] = BandMember.objects.filter(
            ~Exists(GigAttendance.objects.filter(member=OuterRef("pk"), gig=gig)),
            user__is_active=True
        ).order_by('user__first_name', 'user__last_name')

        return context


class GigPartAssignmentOverrideForm(forms.ModelForm):
    class Meta:
        model = GigPartAssignmentOverride
        fields = ['member', 'song_part', 'gig_instrument', 'performance_readiness']

    def __init__(self, *args, **kwargs):
        self.gig_id = kwargs.pop('gig_id')
        super().__init__(*args, **kwargs)

        self.fields['member'].queryset = BandMember.objects.filter(Exists(GigAttendance.objects.filter(member=OuterRef("pk"),
                                                                                                       gig_id=self.gig_id,
                                                                                                       status=GigAttendance.AVAILABLE)),
                                                                   user__is_active=True)
        self.fields['song_part'].queryset = SongPart.objects.filter(song__in_gig_rotation=True).order_by('song', '_order')
        self.fields['gig_instrument'].queryset = GigInstrument.objects.filter(gig_id=self.gig_id)


class GigPartAssignmentOverrideCreateView(generic.CreateView):
    model = GigPartAssignmentOverride
    form_class = GigPartAssignmentOverrideForm
    template_name_suffix = '_create_form'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['gig_id'] = self.kwargs['pk']
        return kwargs

    def get_success_url(self):
        return reverse("band:gig_detail", kwargs={"pk": self.kwargs['pk']})


class InstrumentListView(generic.ListView):
    model = Instrument
