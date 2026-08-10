from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from band.models import (
    BandMember, DrumKitCoverPlayer, Gig, GigAttendance, GigInstrument, GigPartAssignmentOverride,
    GigSetlistEntry, Instrument, OverrideType, PartAssignment, PerformanceReadiness,
    Song, SongPart,
)
from types import SimpleNamespace

from scripts.gig_part_assignment import get_gig_part_assignments, get_max_instrument_usage, get_score
from scripts.coverage_risk import get_coverage_risk, DEFAULT_LOOKBACK
from band.views import GigPartAssignmentOverrideForm


class SongHasSoloUpdateTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Update Solo Song", composer="X", in_gig_rotation=True)
        cls.part = SongPart.objects.create(song=cls.song, name="Lead", has_solo=False)
        cls.trumpet = Instrument.objects.create(name="Trumpet SongSolo", order=0)
        cls.user = User.objects.create_user(username="song_solo_user", first_name="Sue", last_name="Song")
        PartAssignment.objects.create(
            member=cls.user.bandmember,
            song_part=cls.part,
            instrument=cls.trumpet,
            performance_readiness=PerformanceReadiness.READY,
            can_solo=False,
        )

    def test_song_update_can_set_has_solo(self):
        url = reverse("band:song_update", kwargs={"pk": self.song.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = {
            "title": self.song.title,
            "composer": self.song.composer,
            "duration": "",
            "in_gig_rotation": "on",
            "form": self.song.form,
            "parts-TOTAL_FORMS": "1",
            "parts-INITIAL_FORMS": "1",
            "parts-MIN_NUM_FORMS": "0",
            "parts-MAX_NUM_FORMS": "1000",
            "parts-0-id": str(self.part.pk),
            "parts-0-has_solo": "on",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.part.refresh_from_db()
        self.assertTrue(self.part.has_solo)


class GigRecommendationScoreTestCase(TestCase):
    def test_fewer_missing_parts_always_scores_higher(self):
        for missing_parts in range(10):
            worst_score = get_score(missing_parts, instrument_fit=0)
            best_next_score = get_score(missing_parts + 1, instrument_fit=1)

            self.assertGreater(worst_score, best_next_score)

    def test_scarcer_instrument_fit_scores_higher_within_band(self):
        lower_fit = get_score(num_missing_parts=2, instrument_fit=0.25)
        higher_fit = get_score(num_missing_parts=2, instrument_fit=0.75)

        self.assertGreater(higher_fit, lower_fit)

    def test_perfect_assignment_scores_100(self):
        self.assertEqual(get_score(num_missing_parts=0, instrument_fit=1), 100)

    def test_instrument_fit_is_clamped(self):
        self.assertEqual(
            get_score(num_missing_parts=1, instrument_fit=-1),
            get_score(num_missing_parts=1, instrument_fit=0),
        )
        self.assertEqual(
            get_score(num_missing_parts=1, instrument_fit=2),
            get_score(num_missing_parts=1, instrument_fit=1),
        )


class SoloAwareAssignmentTestCase(TestCase):
    """Optimizer prefers can_solo on has_solo parts without changing score formula."""

    @classmethod
    def setUpTestData(cls):
        cls.trumpet = Instrument.objects.create(name="Trumpet SoloOpt", quantity=2, order=0)
        cls.mellophone = Instrument.objects.create(name="Mellophone SoloOpt", quantity=1, order=1)

        cls.alice = User.objects.create_user(username="solo_opt_alice", first_name="Alice", last_name="A")
        cls.bob = User.objects.create_user(username="solo_opt_bob", first_name="Bob", last_name="B")
        cls.carol = User.objects.create_user(username="solo_opt_carol", first_name="Carol", last_name="C")

    def _gig_with_instruments(self, instruments_and_qty):
        gig = Gig.objects.create(
            name="Solo Opt Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        for instrument, qty in instruments_and_qty:
            GigInstrument.objects.create(gig=gig, instrument=instrument, gig_quantity=qty)
        for member in BandMember.objects.filter(user__username__startswith="solo_opt_"):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
        return gig

    def test_prefers_soloist_over_non_soloist_same_instrument(self):
        song = Song.objects.create(title="Solo Prefer Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=True,
        )
        PartAssignment.objects.create(
            member=self.bob.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        players = setlist[0].part_assignments
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0].member, self.alice.bandmember)

    def test_covers_solo_part_with_non_soloist_when_needed(self):
        song = Song.objects.create(title="Solo Fallback Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.bob.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        self.assertEqual(len(setlist[0].part_assignments), 1)
        self.assertEqual(setlist[0].part_assignments[0].member, self.bob.bandmember)
        self.assertEqual(setlist[0].unplayed_parts, set())

    def test_solo_preference_beats_scarcer_instrument(self):
        # Alice must choose between soloing on plentiful trumpet and scarce mellophone fit.
        song = Song.objects.create(title="Solo Vs Scarcity Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=True,
        )
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.mellophone,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 2), (self.mellophone, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        self.assertEqual(len(setlist[0].part_assignments), 1)
        alice_assignment = setlist[0].part_assignments[0]
        self.assertEqual(alice_assignment.member, self.alice.bandmember)
        self.assertEqual(alice_assignment.instrument, self.trumpet)

    def test_solo_preference_never_sacrifices_coverable_part(self):
        song = Song.objects.create(title="Coverage Bound Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        pad = SongPart.objects.create(song=song, name="Pad", has_solo=False)
        trombone = Instrument.objects.create(name="Trombone SoloOpt", quantity=1, order=2)
        for member, instrument in (
            (self.alice.bandmember, self.trumpet),
            (self.bob.bandmember, self.mellophone),
            (self.carol.bandmember, trombone),
        ):
            PartAssignment.objects.create(
                member=member, song_part=lead, instrument=instrument,
                performance_readiness=PerformanceReadiness.READY, can_solo=True,
            )
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=pad, instrument=self.mellophone,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([
            (self.trumpet, 1),
            (self.mellophone, 1),
            (trombone, 1),
        ])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        self.assertEqual(setlist[0].unplayed_parts, set())

    def test_backup_soloists_do_not_displace_ready_player(self):
        song = Song.objects.create(title="Ready Beats Backup Solo Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.mellophone,
            performance_readiness=PerformanceReadiness.BACKUP, can_solo=True,
        )
        PartAssignment.objects.create(
            member=self.bob.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.BACKUP, can_solo=True,
        )
        gig = self._gig_with_instruments([(self.trumpet, 1), (self.mellophone, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        self.assertEqual(len(setlist[0].part_assignments), 1)
        self.assertEqual(
            setlist[0].part_assignments[0].performance_readiness,
            PerformanceReadiness.READY,
        )

    def test_covering_part_still_beats_solo_preference(self):
        # Two parts. Alice is the only person who can cover Pad, and can also solo on Lead.
        # Bob can cover Lead but cannot solo. Prefer covering both parts over putting Alice on Lead.
        song = Song.objects.create(title="Cover Beats Solo Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        pad = SongPart.objects.create(song=song, name="Pad", has_solo=False)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=True,
        )
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=pad, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        PartAssignment.objects.create(
            member=self.bob.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 2)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        gpa = setlist[0]
        self.assertEqual(gpa.unplayed_parts, set())
        by_part = {pa.song_part_id: pa.member for pa in gpa.part_assignments}
        self.assertEqual(by_part[pad.id], self.alice.bandmember)
        self.assertEqual(by_part[lead.id], self.bob.bandmember)

    def test_song_score_ignores_solo_fields(self):
        # Same coverage and instruments → same score whether can_solo is set or not.
        song = Song.objects.create(title="Score Unchanged Song", in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=self.alice.bandmember, song_part=lead, instrument=self.trumpet,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        gig = self._gig_with_instruments([(self.trumpet, 1)])
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        score_without = setlist[0].score

        PartAssignment.objects.filter(song_part=lead).update(can_solo=True)
        setlist2, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist2[0].score, score_without)


class GigPartAssignmentSoloMarkerTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.trumpet = Instrument.objects.create(name="Trumpet Marker", quantity=1, order=0)
        cls.alice = User.objects.create_user(username="solo_marker_alice", first_name="Alice", last_name="M")
        cls.song = Song.objects.create(title="Solo Marker Song", in_gig_rotation=True)
        cls.lead = SongPart.objects.create(song=cls.song, name="Lead", has_solo=True)
        PartAssignment.objects.create(
            member=cls.alice.bandmember,
            song_part=cls.lead,
            instrument=cls.trumpet,
            performance_readiness=PerformanceReadiness.READY,
            can_solo=True,
        )
        cls.gig = Gig.objects.create(
            name="Solo Marker Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.trumpet, gig_quantity=1)
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.alice.bandmember, status=GigAttendance.AVAILABLE,
        )
        GigSetlistEntry.objects.create(gig=cls.gig, song=cls.song)

    def test_gig_part_assignments_mark_soloist_member(self):
        response = self.client.get(
            reverse("band:gig_part_assignments_detail", kwargs={"pk": self.gig.pk})
        )
        self.assertContains(
            response, 'Alice M <span class="solo-marker" title="Can solo">(solo)</span>', html=False
        )

    def test_gig_part_assignments_do_not_mark_solo_parts(self):
        response = self.client.get(
            reverse("band:gig_part_assignments_detail", kwargs={"pk": self.gig.pk})
        )
        self.assertEqual(response.content.decode().count("solo-marker"), 1)


class MemberDetailSoloColumnTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.trumpet = Instrument.objects.create(name="Trumpet MemberSolo", quantity=1, order=0)
        cls.member = User.objects.create_user(
            username="member_solo_user", first_name="Ben", last_name="Hills"
        )
        cls.song = Song.objects.create(title="Member Solo Song", in_gig_rotation=True)
        cls.lead = SongPart.objects.create(song=cls.song, name="Lead", has_solo=True)
        cls.pad = SongPart.objects.create(song=cls.song, name="Pad", has_solo=False)
        PartAssignment.objects.create(
            member=cls.member.bandmember,
            song_part=cls.lead,
            instrument=cls.trumpet,
            performance_readiness=PerformanceReadiness.READY,
            can_solo=True,
        )
        PartAssignment.objects.create(
            member=cls.member.bandmember,
            song_part=cls.pad,
            instrument=cls.trumpet,
            performance_readiness=PerformanceReadiness.READY,
            can_solo=False,
        )

    def test_member_detail_shows_solo_column_marker(self):
        response = self.client.get(
            reverse("band:member_detail", kwargs={"pk": self.member.bandmember.pk})
        )
        self.assertContains(response, "<th>Solo</th>", html=False)
        self.assertContains(
            response, '<span class="solo-marker" title="Can solo">(solo)</span>', html=False
        )
        self.assertEqual(response.content.decode().count("solo-marker"), 1)


class GigRecommendationGroupingTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.drums = Instrument.objects.create(name="Drums", order=0)
        cls.guitar = Instrument.objects.create(name="Guitar", order=1)

        cls.alice = User.objects.create_user(username="group_alice", first_name="Alice", last_name="Smith")
        cls.bob = User.objects.create_user(username="group_bob", first_name="Bob", last_name="Jones")

        cls.gig = Gig.objects.create(
            name="Grouping Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.drums, gig_quantity=1)
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.guitar, gig_quantity=1)

        for member in BandMember.objects.all():
            GigAttendance.objects.create(gig=cls.gig, member=member, status=GigAttendance.AVAILABLE)

        covered_song = Song.objects.create(title="Covered Song", in_gig_rotation=True)
        covered_drums = SongPart.objects.create(song=covered_song, name="Drums Part")
        covered_guitar = SongPart.objects.create(song=covered_song, name="Guitar Part")
        PartAssignment.objects.create(
            member=cls.alice.bandmember,
            song_part=covered_drums,
            instrument=cls.drums,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.bob.bandmember,
            song_part=covered_guitar,
            instrument=cls.guitar,
            performance_readiness=PerformanceReadiness.READY,
        )

        incomplete_song = Song.objects.create(title="Incomplete Song", in_gig_rotation=True)
        incomplete_drums = SongPart.objects.create(song=incomplete_song, name="Drums Part")
        SongPart.objects.create(song=incomplete_song, name="Nobody Plays This Part")
        PartAssignment.objects.create(
            member=cls.alice.bandmember,
            song_part=incomplete_drums,
            instrument=cls.drums,
            performance_readiness=PerformanceReadiness.READY,
        )

    def test_recommendations_are_grouped_by_part_coverage(self):
        response = self.client.get(reverse('band:gig_part_assignments_detail', kwargs={'pk': self.gig.pk}))
        content = response.content.decode()

        self.assertContains(response, "All Parts Covered")
        self.assertContains(response, "Missing Parts")
        self.assertLess(content.index("All Parts Covered"), content.index("Missing Parts"))
        self.assertLess(content.index("Covered Song"), content.index("Missing Parts"))
        self.assertLess(content.index("Missing Parts"), content.index("Incomplete Song"))

    def test_group_heading_renders_once_per_group(self):
        response = self.client.get(reverse('band:gig_part_assignments_detail', kwargs={'pk': self.gig.pk}))
        content = response.content.decode()

        self.assertEqual(content.count("All Parts Covered"), 1)
        self.assertEqual(content.count("Missing Parts"), 1)


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


class MaxInstrumentUsageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lead = Instrument.objects.create(name="Lead", order=0)
        cls.tenor = Instrument.objects.create(name="Double Tenor", order=1)
        cls.congas = Instrument.objects.create(name="Congas", order=2)

        cls.gig = Gig.objects.create(
            name="Max Usage Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        cls.gi_lead = GigInstrument.objects.create(gig=cls.gig, instrument=cls.lead, gig_quantity=7)
        cls.gi_tenor = GigInstrument.objects.create(gig=cls.gig, instrument=cls.tenor, gig_quantity=1)
        cls.gi_congas = GigInstrument.objects.create(gig=cls.gig, instrument=cls.congas, gig_quantity=1)
        cls.gig_instruments = [cls.gi_lead, cls.gi_tenor, cls.gi_congas]

    def _gpa(self, *instruments):
        """Minimal stand-in: helper only reads part_assignments[].instrument."""
        return SimpleNamespace(
            part_assignments=[SimpleNamespace(instrument=inst) for inst in instruments]
        )

    def test_peak_across_songs(self):
        gpas = [
            self._gpa(self.lead, self.lead, self.lead),           # 3 Lead
            self._gpa(self.lead, self.lead, self.lead, self.lead, self.lead),  # 5 Lead
        ]
        result = get_max_instrument_usage(gpas, self.gig_instruments)
        by_name = {inst.name: (max_used, available) for inst, max_used, available in result}
        self.assertEqual(by_name["Lead"], (5, 7))

    def test_unused_instrument_shows_zero(self):
        gpas = [self._gpa(self.lead, self.tenor)]
        result = get_max_instrument_usage(gpas, self.gig_instruments)
        by_name = {inst.name: (max_used, available) for inst, max_used, available in result}
        self.assertEqual(by_name["Congas"], (0, 1))
        self.assertEqual([inst.name for inst, _, _ in result], ["Lead", "Double Tenor", "Congas"])

    def test_empty_assignments_all_zero(self):
        result = get_max_instrument_usage([], self.gig_instruments)
        self.assertEqual(
            [(inst.name, max_used, available) for inst, max_used, available in result],
            [("Lead", 0, 7), ("Double Tenor", 0, 1), ("Congas", 0, 1)],
        )


class GigIsSmallGroupTestCase(TestCase):
    def test_default_is_full_band(self):
        gig = Gig.objects.create(
            name="Full Band Gig",
            start_datetime=timezone.now() - timedelta(days=1),
            end_datetime=timezone.now() - timedelta(days=1) + timedelta(hours=2),
        )
        self.assertFalse(gig.is_small_group)

    def test_can_mark_small_group(self):
        gig = Gig.objects.create(
            name="Small Gig",
            start_datetime=timezone.now() - timedelta(days=1),
            end_datetime=timezone.now() - timedelta(days=1) + timedelta(hours=2),
            is_small_group=True,
        )
        self.assertTrue(Gig.objects.get(pk=gig.pk).is_small_group)


class CoverageRiskScoringTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.lead = Instrument.objects.create(name="Lead", order=0)
        cls.bass = Instrument.objects.create(name="Bass", order=1)

        cls.alice_user = User.objects.create_user(username="cr_alice", first_name="Alice", last_name="A")
        cls.bob_user = User.objects.create_user(username="cr_bob", first_name="Bob", last_name="B")
        cls.cara_user = User.objects.create_user(username="cr_cara", first_name="Cara", last_name="C")
        cls.alice = BandMember.objects.get(user=cls.alice_user)
        cls.bob = BandMember.objects.get(user=cls.bob_user)
        cls.cara = BandMember.objects.get(user=cls.cara_user)

        cls.song = Song.objects.create(title="Yellow Bird", in_gig_rotation=True)
        cls.other = Song.objects.create(title="Not In Rotation", in_gig_rotation=False)
        cls.part_lead = SongPart.objects.create(song=cls.song, name="Lead")
        cls.part_bass = SongPart.objects.create(song=cls.song, name="Bass")
        SongPart.objects.create(song=cls.other, name="Lead")

    def _past_gig(self, days_ago, *, small=False, name=None):
        start = self.now - timedelta(days=days_ago)
        return Gig.objects.create(
            name=name or f"Gig-{days_ago}",
            start_datetime=start,
            end_datetime=start + timedelta(hours=2),
            is_small_group=small,
        )

    def test_zero_gigs_attendance_rate_is_one(self):
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        self.assertEqual(report.lookback_gigs_used, 0)
        song = report.songs[0]
        lead = next(p for p in song.parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 1.0)
        self.assertAlmostEqual(lead.coverers[0].attendance_rate, 1.0)

    def test_small_group_and_future_gigs_excluded(self):
        full = self._past_gig(2, name="Full")
        self._past_gig(1, small=True, name="Small")
        future = Gig.objects.create(
            name="Future",
            start_datetime=self.now + timedelta(days=3),
            end_datetime=self.now + timedelta(days=3, hours=2),
        )
        GigAttendance.objects.create(gig=full, member=self.alice, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=future, member=self.alice, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        self.assertEqual(report.lookback_gigs_used, 1)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.coverers[0].attendance_rate, 1.0)

    def test_attendance_denominator_is_lookback_count(self):
        g1 = self._past_gig(3)
        g2 = self._past_gig(2)
        GigAttendance.objects.create(gig=g1, member=self.alice, status=GigAttendance.AVAILABLE)
        # g2: no response for alice
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        self.assertEqual(report.lookback_gigs_used, 2)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 0.5)
        self.assertAlmostEqual(lead.coverers[0].attendance_rate, 0.5)

    def test_backup_weight_and_not_ready_ignored(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=gig, member=self.bob, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.BACKUP,
        )
        PartAssignment.objects.create(
            member=self.bob, song_part=self.part_lead, instrument=self.bass,
            performance_readiness=PerformanceReadiness.NOT_READY,
        )
        report = get_coverage_risk(lookback=10)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 0.5)  # backup * 1.0
        self.assertEqual(len(lead.coverers), 1)

    def test_one_contribution_per_member_best_readiness(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.BACKUP,
        )
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.bass,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 1.0)
        self.assertEqual(len(lead.coverers), 2)

    def test_effective_coverage_sums_contributions_across_members(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=gig, member=self.bob, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=self.bob, song_part=self.part_lead, instrument=self.bass,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 2.0)
        self.assertEqual(len(lead.coverers), 2)

    def test_member_contributes_to_multiple_parts(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_bass, instrument=self.bass,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        song = next(s for s in report.songs if s.song.id == self.song.id)
        by_part = {p.song_part.name: p.effective_coverage for p in song.parts}
        self.assertAlmostEqual(by_part["Lead"], 1.0)
        self.assertAlmostEqual(by_part["Bass"], 1.0)

    def test_song_risk_is_worst_part_rotation_only(self):
        gig = self._past_gig(1)
        GigAttendance.objects.create(gig=gig, member=self.alice, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        # Bass uncovered
        report = get_coverage_risk(lookback=10)
        titles = [s.song.title for s in report.songs]
        self.assertIn("Yellow Bird", titles)
        self.assertNotIn("Not In Rotation", titles)
        song = next(s for s in report.songs if s.song.title == "Yellow Bird")
        self.assertEqual(song.worst_part_name, "Bass")
        self.assertAlmostEqual(song.risk, 1 / 0.01)

    def test_unassigned_members_on_song(self):
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        song = next(s for s in report.songs if s.song.id == self.song.id)
        names = {m.user.username for m in song.unassigned_members}
        self.assertNotIn("cr_alice", names)
        self.assertIn("cr_bob", names)
        self.assertIn("cr_cara", names)

    def test_inactive_member_excluded_from_coverers_and_coverage(self):
        inactive_user = User.objects.create_user(
            username="cr_inactive", first_name="In", last_name="Active", is_active=False,
        )
        inactive_member = BandMember.objects.get(user=inactive_user)
        PartAssignment.objects.create(
            member=inactive_member, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        song = next(s for s in report.songs if s.song.id == self.song.id)
        lead = next(p for p in song.parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 0.0)
        self.assertEqual(len(lead.coverers), 0)
        member_names = {c.member.user.username for c in lead.coverers}
        self.assertNotIn("cr_inactive", member_names)

    def test_instrument_excluded_from_coverage_risk_omits_part(self):
        drumset = Instrument.objects.create(
            name="Drumset", order=2, include_in_coverage_risk=False,
        )
        engine = SongPart.objects.create(song=self.song, name="Engine Room")
        PartAssignment.objects.create(
            member=self.alice, song_part=engine, instrument=drumset,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        song = next(s for s in report.songs if s.song.id == self.song.id)
        part_names = {p.song_part.name for p in song.parts}
        self.assertNotIn("Engine Room", part_names)
        self.assertIn("Lead", part_names)
        self.assertEqual(song.worst_part_name, "Bass")  # uncovered pan part

    def test_excluded_instrument_does_not_count_toward_coverage(self):
        """If a part mixes included + excluded instruments, only included count."""
        drumset = Instrument.objects.create(
            name="Drumset", order=2, include_in_coverage_risk=False,
        )
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=self.bob, song_part=self.part_lead, instrument=drumset,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(lookback=10)
        lead = next(
            p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id
        )
        self.assertAlmostEqual(lead.effective_coverage, 1.0)
        self.assertEqual(len(lead.coverers), 1)
        self.assertEqual(lead.coverers[0].member.user.username, "cr_alice")


class CoverageRiskViewsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="viewer", password="pass")
        Song.objects.create(title="Rotation Song", in_gig_rotation=True)

    def test_reports_hub_ok(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:reports"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Coverage Risk")

    def test_coverage_risk_ok_default_n(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:coverage_risk"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["lookback"], 10)

    def test_coverage_risk_n_query_param(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:coverage_risk"), {"n": "5"})
        self.assertEqual(resp.context["lookback"], 5)

    def test_coverage_risk_invalid_n_defaults(self):
        self.client.login(username="viewer", password="pass")
        for bad in ["0", "-3", "abc"]:
            resp = self.client.get(reverse("band:coverage_risk"), {"n": bad})
            self.assertEqual(resp.context["lookback"], 10, msg=bad)

    def test_coverage_risk_huge_n_clamped(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:coverage_risk"), {"n": "999999"})
        self.assertEqual(resp.context["lookback"], 200)


class GigListPastPaginationTestCase(TestCase):
    def _past_gig(self, days_ago, name=None):
        start = timezone.now() - timedelta(days=days_ago)
        return Gig.objects.create(
            name=name or f"Past-{days_ago}",
            start_datetime=start,
            end_datetime=start + timedelta(hours=2),
        )

    def _upcoming_gig(self, days_ahead, name=None):
        start = timezone.now() + timedelta(days=days_ahead)
        return Gig.objects.create(
            name=name or f"Upcoming-{days_ahead}",
            start_datetime=start,
            end_datetime=start + timedelta(hours=2),
        )

    def test_first_page_has_at_most_ten_past_gigs(self):
        for i in range(1, 16):
            self._past_gig(i, name=f"P{i}")
        self._upcoming_gig(1, name="U1")

        resp = self.client.get(reverse("band:gig_list"))
        self.assertEqual(resp.status_code, 200)
        past = list(resp.context["past_gigs"])
        self.assertEqual(len(past), 10)
        # Newest past first (days_ago=1 … 10)
        self.assertEqual([g.name for g in past], [f"P{i}" for i in range(1, 11)])
        upcoming = list(resp.context["upcoming_gigs"])
        self.assertEqual([g.name for g in upcoming], ["U1"])
        self.assertEqual(resp.context["page_obj"].number, 1)
        self.assertEqual(resp.context["page_obj"].paginator.num_pages, 2)

    def test_page_two_returns_remaining_past_gigs(self):
        for i in range(1, 16):
            self._past_gig(i, name=f"P{i}")

        resp = self.client.get(reverse("band:gig_list"), {"page": "2"})
        self.assertEqual(resp.status_code, 200)
        past = list(resp.context["past_gigs"])
        self.assertEqual([g.name for g in past], [f"P{i}" for i in range(11, 16)])
        self.assertEqual(resp.context["page_obj"].number, 2)

    def test_invalid_page_still_returns_200(self):
        for i in range(1, 12):
            self._past_gig(i, name=f"P{i}")

        for bad in ("999", "abc", "-1", "0"):
            with self.subTest(page=bad):
                resp = self.client.get(reverse("band:gig_list"), {"page": bad})
                self.assertEqual(resp.status_code, 200)
                self.assertIn("page_obj", resp.context)
                self.assertTrue(1 <= resp.context["page_obj"].number <= 2)

    def test_pagination_controls_when_multiple_pages(self):
        for i in range(1, 12):
            self._past_gig(i, name=f"P{i}")

        resp = self.client.get(reverse("band:gig_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Previous", count=0)  # page 1: no Previous
        self.assertContains(resp, "Next")
        self.assertContains(resp, 'href="?page=2"')
        # Current page is not a link to itself as ?page=1 in a way that duplicates;
        # at minimum page 2 link exists and "1" appears as current.
        self.assertContains(resp, "<strong>1</strong>")

        resp2 = self.client.get(reverse("band:gig_list"), {"page": "2"})
        self.assertContains(resp2, "Previous")
        self.assertContains(resp2, 'href="?page=1"')
        self.assertContains(resp2, "Next", count=0)
        self.assertContains(resp2, "<strong>2</strong>")

    def test_no_pagination_controls_when_single_page(self):
        for i in range(1, 6):
            self._past_gig(i, name=f"P{i}")

        resp = self.client.get(reverse("band:gig_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Previous")
        self.assertNotContains(resp, "Next")
        self.assertNotContains(resp, 'href="?page=')


class SoloFieldsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Solo Fields Song", in_gig_rotation=True)
        cls.solo_part = SongPart.objects.create(song=cls.song, name="Lead", has_solo=True)
        cls.plain_part = SongPart.objects.create(song=cls.song, name="Pad", has_solo=False)
        cls.trumpet = Instrument.objects.create(name="Trumpet SoloFields", order=0)
        cls.user = User.objects.create_user(username="solo_fields_user", first_name="Sam", last_name="Solo")

    def test_defaults_are_false(self):
        part = SongPart.objects.create(song=self.song, name="Default Part")
        self.assertFalse(part.has_solo)
        assignment = PartAssignment(
            member=self.user.bandmember,
            song_part=self.solo_part,
            instrument=self.trumpet,
        )
        self.assertFalse(assignment.can_solo)

    def test_can_solo_rejected_when_part_has_no_solo(self):
        assignment = PartAssignment(
            member=self.user.bandmember,
            song_part=self.plain_part,
            instrument=self.trumpet,
            can_solo=True,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_can_solo_allowed_when_part_has_solo(self):
        assignment = PartAssignment(
            member=self.user.bandmember,
            song_part=self.solo_part,
            instrument=self.trumpet,
            can_solo=True,
            performance_readiness=PerformanceReadiness.READY,
        )
        assignment.full_clean()
        assignment.save()
        self.assertTrue(PartAssignment.objects.get(pk=assignment.pk).can_solo)

    def test_clearing_has_solo_clears_can_solo(self):
        assignment = PartAssignment.objects.create(
            member=self.user.bandmember,
            song_part=self.solo_part,
            instrument=self.trumpet,
            can_solo=True,
            performance_readiness=PerformanceReadiness.READY,
        )
        self.solo_part.has_solo = False
        self.solo_part.save()
        assignment.refresh_from_db()
        self.assertFalse(assignment.can_solo)
        self.assertFalse(SongPart.objects.get(pk=self.solo_part.pk).has_solo)


class PartAssignmentCanSoloFormTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Form Solo Song", in_gig_rotation=True)
        cls.solo_part = SongPart.objects.create(song=cls.song, name="Lead", has_solo=True)
        cls.plain_part = SongPart.objects.create(song=cls.song, name="Pad", has_solo=False)
        cls.trumpet = Instrument.objects.create(name="Trumpet FormSolo", order=0)
        cls.user = User.objects.create_user(username="form_solo_user", first_name="Fran", last_name="Form")

    def test_form_rejects_can_solo_on_non_solo_part(self):
        from band.views import PartAssignmentForm
        form = PartAssignmentForm(data={
            "member": self.user.bandmember.pk,
            "song_part": self.plain_part.pk,
            "instrument": self.trumpet.pk,
            "performance_readiness": PerformanceReadiness.READY,
            "can_solo": True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("can_solo", form.errors)

    def test_form_accepts_can_solo_on_solo_part(self):
        from band.views import PartAssignmentForm
        form = PartAssignmentForm(data={
            "member": self.user.bandmember.pk,
            "song_part": self.solo_part.pk,
            "instrument": self.trumpet.pk,
            "performance_readiness": PerformanceReadiness.READY,
            "can_solo": True,
        })
        self.assertTrue(form.is_valid(), form.errors)


class DrumKitCoverModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kit = Instrument.objects.create(
            name="KitCoverModel Drum Set", order=0, is_drum_kit_cover_instrument=True,
        )
        cls.other = Instrument.objects.create(name="KitCoverModel Congas", order=1)
        cls.user = User.objects.create_user(
            username="kit_cover_model_user", first_name="Kit", last_name="Cover",
        )

    def test_instrument_flag_default_false(self):
        inst = Instrument.objects.create(name="KitCoverModel Default", order=2)
        self.assertFalse(inst.is_drum_kit_cover_instrument)

    def test_drum_kit_cover_player_unique_member_and_str(self):
        row = DrumKitCoverPlayer.objects.create(
            member=self.user.bandmember, priority=1,
        )
        self.assertEqual(str(row), "Kit Cover (priority 1)")
        dup = DrumKitCoverPlayer(member=self.user.bandmember, priority=2)
        with self.assertRaises(Exception):
            dup.save()
