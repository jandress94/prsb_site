from django.db.models import Exists, OuterRef
from django.forms import forms
from django.http import HttpResponse
from django.shortcuts import render
from django.views import generic

from scripts.gig_part_assignment import get_gig_part_assignments
from .models import Song, Gig, GigAttendance, BandMember, PartAssignment, Instrument, SongPart


def index(request):
    return render(request, "band/index.html")


class MemberListView(generic.ListView):
    def get_queryset(self):
        return BandMember.objects.filter(user__is_active=True).order_by('user__first_name', 'user__last_name')


class MemberDetailView(generic.DetailView):
    model = BandMember

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        current_member = context['bandmember']

        context["part_assignments"] = PartAssignment.objects.filter(
            song_part__song__in_gig_rotation=True,
            member=current_member
        ).order_by('song_part__song__title', 'song_part___order', 'instrument__name')

        context["upcoming_gig_attendance"] = GigAttendance.objects.filter(
            member=current_member
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
    def get_queryset(self):
        return Song.objects.filter(in_gig_rotation=True).order_by('title')


class SongDetailView(generic.DetailView):
    model = Song

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["part_assignments"] = PartAssignment.objects.filter(
            song_part__song=context['song']
        ).order_by('song_part___order', 'instrument__name', 'member__user__first_name', 'member__user__last_name')

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


# class PartAssignmentListView(generic.ListView):
#     model = PartAssignment
#     ordering = ['song_part__song__title',
#                 'song_part___order',
#                 'instrument__name',
#                 'member__user__first_name',
#                 'member__user__last_name']


class GigListView(generic.ListView):
    model = Gig


class GigDetailView(generic.DetailView):
    model = Gig

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["gig_part_assignments"] = get_gig_part_assignments(context['object'])

        for availability in GigAttendance.AVAILABILITY_CHOICES:
            members = context['object'].gigattendance_set.filter(status=availability)
            context[f"{availability}_members"] = sorted(members, key=lambda ga: ga.member.user.get_full_name())

        context["missing_members"] = BandMember.objects.filter(
            ~Exists(GigAttendance.objects.filter(member=OuterRef("pk"), gig=context['object'])),
            user__is_active=True
        ).order_by('user__first_name', 'user__last_name')

        return context


class InstrumentListView(generic.ListView):
    model = Instrument
