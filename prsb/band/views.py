from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Exists, OuterRef, Subquery
from django import forms
from django.db import connection
from django.forms import modelformset_factory, formset_factory, inlineformset_factory
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import generic
from tinymce.models import HTMLField

from scripts.gig_part_assignment import get_gig_part_assignments, GigPartAssignment
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
        context['upcoming_gigs'] = Gig.objects.filter(end_datetime__gte=timezone.now()).order_by('start_datetime')
        context['past_gigs'] = Gig.objects.filter(end_datetime__lt=timezone.now())
        return context


class GigForm(forms.ModelForm):
    class Meta:
        model = Gig
        fields = ['name',
                  'start_datetime',
                  'end_datetime',
                  'address',
                  'notes']

    # Override the widget for the DateTimeField
    start_datetime = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'})
    )
    end_datetime = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'})
    )
    notes = HTMLField()


class GigInstrumentForm(forms.ModelForm):
    class Meta:
        model = GigInstrument
        fields = ['instrument', 'gig_quantity']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['instrument'].disabled = True

GigInstrumentFormSet = inlineformset_factory(
    Gig,
    GigInstrument,  # Related model
    form=GigInstrumentForm,
    extra=0
)


class GigCreateView(generic.CreateView):
    model = Gig
    form_class = GigForm
    template_name_suffix = '_update_form'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_create'] = True

        if self.request.POST:
            context['instrument_formset'] = GigInstrumentFormSet(self.request.POST)
        else:
            context['instrument_formset'] = GigInstrumentFormSet()
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        instrument_formset = context['instrument_formset']

        if form.is_valid() and instrument_formset.is_valid():
            self.object = form.save()  # Save the Gig instance
            instrument_formset.instance = self.object  # Link the formset to this Gig
            instrument_formset.save()  # Save the related GigInstrument instances
            return redirect(self.get_success_url())

        return self.form_invalid(form)

    def get_success_url(self):
        return reverse("band:gig_detail", kwargs={'pk': self.object.pk})


class GigDetailView(generic.DetailView):
    model = Gig

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gig = context['object']

        context['setlist'] = set_list = GigSetlistEntry.objects.filter(gig=gig)

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

        for availability in GigAttendance.AVAILABILITY_CHOICES:
            members = gig.gigattendance_set.filter(status=availability)
            context[f"{availability}_members"] = sorted(members, key=lambda ga: ga.member.user.get_full_name())

        context["missing_members"] = BandMember.objects.filter(
            ~Exists(GigAttendance.objects.filter(member=OuterRef("pk"), gig=gig)),
            user__is_active=True
        ).order_by('user__first_name', 'user__last_name')

        return context


class GigUpdateView(generic.UpdateView):
    model = Gig
    form_class = GigForm
    template_name_suffix = '_update_form'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_create'] = False

        if self.request.POST:
            context['instrument_formset'] = GigInstrumentFormSet(self.request.POST, instance=self.object)
        else:
            context['instrument_formset'] = GigInstrumentFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        instrument_formset = context['instrument_formset']

        if form.is_valid() and instrument_formset.is_valid():
            self.object = form.save()  # Save the Gig instance
            instrument_formset.instance = self.object  # Link the formset to this Gig
            GigInstrument.objects.filter(gig=self.object).exclude(id__in={form.instance.id for form in instrument_formset.forms}).delete()
            instrument_formset.save()  # Save the related GigInstrument instances
            return redirect(self.get_success_url())

        return self.form_invalid(form)

    def get_success_url(self):
        return reverse("band:gig_detail", kwargs={'pk': self.object.pk})


class GigAvailabilityForm(forms.Form):
    attendance_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    member_id = forms.IntegerField(widget=forms.HiddenInput())
    status = forms.ChoiceField(choices=[(None, '------')] + list(GigAttendance.AVAILABILITY_CHOICES.items()), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print(args, kwargs)

        if 'initial' not in kwargs:
            return

        member = kwargs['initial']['member']

        self.fields['member_id'].initial = member.user_id
        self.member_name = member.user.get_full_name()

GigAvailabilityFormSet = formset_factory(GigAvailabilityForm, extra=0)


class GigAvailabilityUpdateView(generic.View):
    template_name = 'band/gig_availability_update.html'

    def get(self, request, gig_id):
        gig = get_object_or_404(Gig, id=gig_id)

        existing_gig_attendance = GigAttendance.objects.filter(gig=gig)
        initial_data = [
            {
                'attendance_id': ga.id,
                'member': ga.member,
                'status': ga.status
            }
            for ga in existing_gig_attendance
        ]

        missing_members = BandMember.objects.filter(~Exists(GigAttendance.objects.filter(gig=gig, member=OuterRef('pk'))), user__is_active=True)
        initial_data += [
            {
                'attendance_id': None,
                'member': member,
                'status': None
            }
            for member in missing_members
        ]

        initial_data = sorted(initial_data, key=lambda x: x['member'].user.get_full_name())

        formset = GigAvailabilityFormSet(initial=initial_data)
        return render(request, self.template_name, {'gig': gig, 'formset': formset})

    def post(self, request, gig_id):
        gig = get_object_or_404(Gig, id=gig_id)
        formset = GigAvailabilityFormSet(request.POST)

        if formset.is_valid():
            new_gig_attendance = []
            modified_gig_attendance = []
            deleted_gig_attendance = []
            for form in formset:
                if form.cleaned_data['attendance_id'] is not None:
                    if form.cleaned_data['status'] == '':
                        deleted_gig_attendance.append(form)
                    else:   # TODO: only things that have actually changed
                        modified_gig_attendance.append(form)
                elif form.cleaned_data['status'] != '':
                    new_gig_attendance.append(form)

            GigAttendance.objects.bulk_create([GigAttendance(gig=gig, member_id=form.cleaned_data['member_id'], status=form.cleaned_data['status']) for form in new_gig_attendance])
            GigAttendance.objects.bulk_update([GigAttendance(id=form.cleaned_data['attendance_id'], status=form.cleaned_data['status']) for form in modified_gig_attendance], ['status'])
            GigAttendance.objects.filter(id__in=[form.cleaned_data['attendance_id'] for form in deleted_gig_attendance]).delete()

            return redirect('band:gig_detail', pk=gig.id)
        else:
            return render(request, self.template_name, {'formset': formset, 'gig': gig})


class GigSetlistEntryForm(forms.ModelForm):
    class Meta:
        model = GigSetlistEntry
        fields = ['song', 'break_duration']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['song'].queryset = Song.objects.filter(in_gig_rotation=True)

GigSetlistEntryFormSet = modelformset_factory(GigSetlistEntry, form=GigSetlistEntryForm, extra=0, can_delete=True, can_delete_extra=True)


class GigSetlistUpdateView(generic.View):
    template_name = 'band/gig_setlist_update.html'

    def get(self, request, gig_id):
        gig = get_object_or_404(Gig, id=gig_id)
        formset = GigSetlistEntryFormSet(queryset=GigSetlistEntry.objects.filter(gig=gig))
        return render(request, self.template_name, {'formset': formset, 'gig': gig})

    def post(self, request, gig_id):
        gig = get_object_or_404(Gig, id=gig_id)
        formset = GigSetlistEntryFormSet(request.POST)
        formset.clean()

        for i in range(len(formset.forms) - 1, -1, -1):
            if not formset.forms[i].cleaned_data:
                formset.forms.pop(i)

        if formset.is_valid():
            formset.save(commit=False)
            GigSetlistEntry.objects.filter(gig=gig).exclude(id__in={form.instance.id for form in formset.forms}).delete()

            for i, form in enumerate(formset.forms):
                form.instance.gig = gig  # Ensure each entry is linked to the correct gig
                form.instance._order = i
                form.instance.save()

            return redirect('band:gig_detail', pk=gig.id)

        return render(request, self.template_name, {'formset': formset, 'gig': gig})


class GigSetlistAddSongView(generic.View):
    def get(self, request, *args, **kwargs):
        gig_id = kwargs['gig_id']
        setlist_entry = GigSetlistEntry(gig_id=gig_id, song_id=kwargs['song_id'])
        setlist_entry.save()

        return HttpResponseRedirect(reverse("band:gig_part_assignments_detail", kwargs={'pk': gig_id}))


class GigPartAssignmentsDetailView(generic.TemplateView):
    template_name = 'band/gig_part_assignments.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gig_id = context['pk']

        context['gig'] = gig = Gig.objects.get(id=gig_id)

        context['part_assignment_overrides'] = GigPartAssignmentOverride.objects.filter(gig_instrument__gig=gig).order_by('song_part__song', 'song_part', 'member')

        context["gig_part_assignments_setlist"], context["gig_part_assignments_recs"], member_song_counts = get_gig_part_assignments(gig, context['part_assignment_overrides'])
        context['member_song_counts'] = sorted([(k, v) for k, v in member_song_counts.items()], key=lambda x: x[1], reverse=True)

        return context


class GigInstrumentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: GigInstrument):
        return obj.instrument


class GigPartAssignmentOverrideForm(forms.ModelForm):
    class Meta:
        model = GigPartAssignmentOverride
        fields = ['member', 'song_part', 'gig_instrument', 'performance_readiness']

    gig_instrument = GigInstrumentChoiceField(
        queryset=GigInstrument.objects.all(),   # will be overridden below when we know which gig
        label='Instrument'
    )

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
        return reverse("band:gig_part_assignments_detail", kwargs={"pk": self.kwargs['pk']})


class GigPartAssignmentPrintView(generic.TemplateView):
    template_name = 'band/gig_part_assignment_print.html'

    @staticmethod
    def _sort_by_setlist_order(gig: Gig, assignments: list[GigPartAssignment]) -> list[GigPartAssignment]:
        subquery = GigSetlistEntry.objects.filter(
            gig=gig,
            song=OuterRef('song')
        ).order_by('_order').values('id')[:1]
        first_entries = GigSetlistEntry.objects.filter(
            gig=gig,
            break_duration__isnull=True,
            id__in=Subquery(subquery)
        )
        ordering = {entry.song: entry._order for entry in first_entries}

        return sorted(assignments, key=lambda e: ordering.get(e.song))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gig_id = context['pk']

        context['gig'] = gig = Gig.objects.get(id=gig_id)

        overrides = GigPartAssignmentOverride.objects.filter(
            gig_instrument__gig=gig).order_by('song_part__song', 'song_part', 'member')

        assignments, _, _ = get_gig_part_assignments(gig, overrides)

        ordering_method = self.request.GET.get('order', 'alphabetic')
        if ordering_method == 'setlist':
            context["gig_part_assignments_setlist"] = self._sort_by_setlist_order(gig, assignments)
        else:
            context["gig_part_assignments_setlist"] = assignments

        return context


class InstrumentListView(generic.ListView):
    model = Instrument


def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok", "db": "connected"})
    except Exception:
        return JsonResponse({"status": "error", "db": "unreachable"}, status=500)


def get_instruments(request):
    instruments = list(Instrument.objects.all().order_by('order').values('id', 'name', 'quantity'))
    return JsonResponse({"instruments": instruments})
