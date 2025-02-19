from django.db.models import Exists, OuterRef
from django import forms
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from scripts.gig_part_assignment import get_gig_part_assignments
from .models import Song, Gig, GigAttendance, BandMember, PartAssignment, Instrument, SongPart, \
    GigPartAssignmentOverride, GigInstrument


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
        context['upcoming_gigs'] = Gig.objects.filter(start_datetime__gte=timezone.now())
        context['past_gigs'] = Gig.objects.filter(start_datetime__lt=timezone.now())
        return context


class GigDetailView(generic.DetailView):
    model = Gig

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gig = context['object']

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
