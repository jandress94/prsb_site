from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from band.models import (
    BandMember, Gig, GigAttendance, GigInstrument, GigPartAssignmentOverride,
    GigSetlistEntry, Instrument, OverrideType, PartAssignment, PerformanceReadiness,
    Song, SongPart,
)
from scripts.gig_part_assignment import get_gig_part_assignments
from band.views import GigPartAssignmentOverrideForm


class GigPartAssignmentOverrideTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Test Song", in_gig_rotation=True)
        cls.part_a = SongPart.objects.create(song=cls.song, name="Part A")
        cls.part_b = SongPart.objects.create(song=cls.song, name="Part B")

        cls.drums = Instrument.objects.create(name="Drums", order=0)
        cls.guitar = Instrument.objects.create(name="Guitar", order=1)

        cls.member_a = User.objects.create_user(username="alice", first_name="Alice", last_name="Smith")
        cls.member_b = User.objects.create_user(username="bob", first_name="Bob", last_name="Jones")

        cls.gig = Gig.objects.create(
            name="Test Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.drums, gig_quantity=1)
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.guitar, gig_quantity=1)

        for member in BandMember.objects.all():
            GigAttendance.objects.create(gig=cls.gig, member=member, status=GigAttendance.AVAILABLE)

        PartAssignment.objects.create(
            member=cls.member_a.bandmember,
            song_part=cls.part_a,
            instrument=cls.drums,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.member_a.bandmember,
            song_part=cls.part_b,
            instrument=cls.guitar,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.member_b.bandmember,
            song_part=cls.part_a,
            instrument=cls.drums,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.member_b.bandmember,
            song_part=cls.part_b,
            instrument=cls.guitar,
            performance_readiness=PerformanceReadiness.READY,
        )

        GigSetlistEntry.objects.create(gig=cls.gig, song=cls.song)

    def _assignments_for_song(self, overrides):
        setlist, _, _ = get_gig_part_assignments(self.gig, overrides)
        self.assertEqual(len(setlist), 1)
        return setlist[0].part_assignments

    def test_not_playing_override_excludes_member_from_part_instrument(self):
        drums_at_gig = GigInstrument.objects.get(gig=self.gig, instrument=self.drums)
        override = GigPartAssignmentOverride.objects.create(
            member=self.member_a.bandmember,
            song_part=self.part_a,
            gig_instrument=drums_at_gig,
            override_type=OverrideType.NOT_PLAYING,
        )

        assignments = self._assignments_for_song([override])
        drums_players = [
            pa for pa in assignments
            if pa.song_part_id == self.part_a.id and pa.instrument_id == self.drums.id
        ]

        self.assertEqual(len(drums_players), 1)
        self.assertEqual(drums_players[0].member, self.member_b.bandmember)

    def test_assign_override_still_forces_member_to_part(self):
        drums_at_gig = GigInstrument.objects.get(gig=self.gig, instrument=self.drums)
        override = GigPartAssignmentOverride.objects.create(
            member=self.member_a.bandmember,
            song_part=self.part_a,
            gig_instrument=drums_at_gig,
            override_type=OverrideType.ASSIGN,
        )

        assignments = self._assignments_for_song([override])
        drums_players = [
            pa for pa in assignments
            if pa.song_part_id == self.part_a.id and pa.instrument_id == self.drums.id
        ]

        self.assertEqual(len(drums_players), 1)
        self.assertEqual(drums_players[0].member, self.member_a.bandmember)

    def test_not_playing_does_not_exclude_other_part_assignments_for_member(self):
        drums_at_gig = GigInstrument.objects.get(gig=self.gig, instrument=self.drums)
        override = GigPartAssignmentOverride.objects.create(
            member=self.member_a.bandmember,
            song_part=self.part_a,
            gig_instrument=drums_at_gig,
            override_type=OverrideType.NOT_PLAYING,
        )

        assignments = self._assignments_for_song([override])
        guitar_players = [
            pa for pa in assignments
            if pa.song_part_id == self.part_b.id and pa.instrument_id == self.guitar.id
        ]

        self.assertEqual(len(guitar_players), 1)
        self.assertIn(guitar_players[0].member, {self.member_a.bandmember, self.member_b.bandmember})

    def test_not_playing_form_valid_without_performance_readiness(self):
        drums_at_gig = GigInstrument.objects.get(gig=self.gig, instrument=self.drums)
        form = GigPartAssignmentOverrideForm(
            data={
                'override_type': OverrideType.NOT_PLAYING,
                'member': self.member_a.bandmember.pk,
                'song_part': self.part_a.pk,
                'gig_instrument': drums_at_gig.pk,
            },
            gig_id=self.gig.pk,
            gig_name=self.gig.name,
        )

        self.assertTrue(form.is_valid(), form.errors)
        override = form.save()
        self.assertEqual(override.override_type, OverrideType.NOT_PLAYING)
        self.assertEqual(override.performance_readiness, PerformanceReadiness.READY)

    def test_delete_override_from_gig_part_assignments_page(self):
        drums_at_gig = GigInstrument.objects.get(gig=self.gig, instrument=self.drums)
        override = GigPartAssignmentOverride.objects.create(
            member=self.member_a.bandmember,
            song_part=self.part_a,
            gig_instrument=drums_at_gig,
            override_type=OverrideType.NOT_PLAYING,
        )

        response = self.client.post(
            reverse('band:gig_part_assignment_override_delete', kwargs={'pk': self.gig.pk, 'override_id': override.pk}),
        )

        self.assertRedirects(response, reverse('band:gig_part_assignments_detail', kwargs={'pk': self.gig.pk}))
        self.assertFalse(GigPartAssignmentOverride.objects.filter(pk=override.pk).exists())
