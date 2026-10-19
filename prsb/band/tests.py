import html
import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.http import QueryDict
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from djangoyearlessdate.helpers import YearlessDate
from django.test import TestCase, TransactionTestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from band.models import (
    Band, BandMembership, BandMember, BandSong, BandSpecialDate, DrumKitCoverPlayer, Gig, GigAttendance,
    GigInstrument, GigPartAssignmentOverride, GigSetlistEntry, Instrument, OverrideType,
    PartAssignment, PerformanceReadiness, Song, SongPart,
)
from types import SimpleNamespace

from scripts.gig_part_assignment import (
    get_gig_part_assignments, get_max_instrument_usage, get_score,
    inject_drum_kit_cover_assignments,
)
from scripts.coverage_risk import get_coverage_risk, DEFAULT_LOOKBACK
from band.validation import validate_entry_change, validate_gig_bands
from band.views import GigPartAssignmentOverrideForm
PRSB_SLUG = "prsb"


def _link_gig_to_prsb(gig):
    gig.bands.add(Band.objects.get(slug=PRSB_SLUG))
    return gig


from band.dietary_restrictions import (
    STATUS_NO_STATUS,
    dietary_restriction_members,
    gig_picker_rows,
    past_gigs,
    resolve_gig,
    resolve_statuses,
    status_caption_labels,
    upcoming_gigs,
)


class SongHasSoloUpdateTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Update Solo Song", composer="X")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.song,
            in_gig_rotation=True,
        )
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
        url = reverse("band:song_update", kwargs={"slug": PRSB_SLUG, "pk": self.song.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = {
            "title": self.song.title,
            "composer": self.song.composer,
            "duration": "",
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
        prsb = Band.objects.get(slug="prsb")
        for user in (cls.alice, cls.bob, cls.carol):
            BandMembership.objects.create(band=prsb, member=user.bandmember)

    def _gig_with_instruments(self, instruments_and_qty):
        gig = Gig.objects.create(
            name="Solo Opt Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        _link_gig_to_prsb(gig)
        for instrument, qty in instruments_and_qty:
            GigInstrument.objects.create(gig=gig, instrument=instrument, gig_quantity=qty)
        for member in BandMember.objects.filter(user__username__startswith="solo_opt_"):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
        return gig

    def test_prefers_soloist_over_non_soloist_same_instrument(self):
        song = Song.objects.create(title="Solo Prefer Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=song,
            in_gig_rotation=True,
        )
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
        song = Song.objects.create(title="Solo Fallback Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=song,
            in_gig_rotation=True,
        )
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
        self.assertEqual(setlist[0].unplayed_parts, [])

    def test_solo_preference_beats_scarcer_instrument(self):
        # Alice must choose between soloing on plentiful trumpet and scarce mellophone fit.
        song = Song.objects.create(title="Solo Vs Scarcity Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=song,
            in_gig_rotation=True,
        )
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
        song = Song.objects.create(title="Coverage Bound Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=song,
            in_gig_rotation=True,
        )
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
        self.assertEqual(setlist[0].unplayed_parts, [])

    def test_backup_soloists_do_not_displace_ready_player(self):
        song = Song.objects.create(title="Ready Beats Backup Solo Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=song,
            in_gig_rotation=True,
        )
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
        song = Song.objects.create(title="Cover Beats Solo Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=song,
            in_gig_rotation=True,
        )
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
        self.assertEqual(gpa.unplayed_parts, [])
        by_part = {pa.song_part_id: pa.member for pa in gpa.part_assignments}
        self.assertEqual(by_part[pad.id], self.alice.bandmember)
        self.assertEqual(by_part[lead.id], self.bob.bandmember)

    def test_song_score_ignores_solo_fields(self):
        # Same coverage and instruments → same score whether can_solo is set or not.
        song = Song.objects.create(title="Score Unchanged Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=song,
            in_gig_rotation=True,
        )
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
        BandMembership.objects.create(band=Band.objects.get(slug="prsb"), member=cls.alice.bandmember)
        cls.song = Song.objects.create(title="Solo Marker Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.song,
            in_gig_rotation=True,
        )
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
        _link_gig_to_prsb(cls.gig)
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.trumpet, gig_quantity=1)
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.alice.bandmember, status=GigAttendance.AVAILABLE,
        )
        GigSetlistEntry.objects.create(gig=cls.gig, song=cls.song)

    def test_gig_part_assignments_mark_soloist_member(self):
        response = self.client.get(
            reverse("band:gig_part_assignments_detail", kwargs={"slug": PRSB_SLUG, "pk": self.gig.pk})
        )
        self.assertContains(
            response, 'Alice M <span class="solo-marker" title="Can solo">(solo)</span>', html=False
        )

    def test_gig_part_assignments_do_not_mark_solo_parts(self):
        response = self.client.get(
            reverse("band:gig_part_assignments_detail", kwargs={"slug": PRSB_SLUG, "pk": self.gig.pk})
        )
        self.assertEqual(response.content.decode().count("solo-marker"), 1)


class MemberDetailSoloColumnTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.trumpet = Instrument.objects.create(name="Trumpet MemberSolo", quantity=1, order=0)
        cls.member = User.objects.create_user(
            username="member_solo_user", first_name="Ben", last_name="Hills"
        )
        cls.song = Song.objects.create(title="Member Solo Song")
        prsb = Band.objects.get(slug="prsb")
        BandMembership.objects.create(band=prsb, member=cls.member.bandmember)
        BandSong.objects.create(
            band=prsb,
            song=cls.song,
            in_gig_rotation=True,
        )
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
            reverse("band:member_detail", kwargs={"slug": PRSB_SLUG, "pk": self.member.bandmember.pk})
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
        prsb = Band.objects.get(slug="prsb")
        for user in (cls.alice, cls.bob):
            BandMembership.objects.create(band=prsb, member=user.bandmember)

        cls.gig = Gig.objects.create(
            name="Grouping Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        _link_gig_to_prsb(cls.gig)
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.drums, gig_quantity=1)
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.guitar, gig_quantity=1)

        for member in BandMember.objects.all():
            GigAttendance.objects.create(gig=cls.gig, member=member, status=GigAttendance.AVAILABLE)

        covered_song = Song.objects.create(title="Covered Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=covered_song,
            in_gig_rotation=True,
        )
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

        incomplete_song = Song.objects.create(title="Incomplete Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=incomplete_song,
            in_gig_rotation=True,
        )
        incomplete_drums = SongPart.objects.create(song=incomplete_song, name="Drums Part")
        SongPart.objects.create(song=incomplete_song, name="Nobody Plays This Part")
        PartAssignment.objects.create(
            member=cls.alice.bandmember,
            song_part=incomplete_drums,
            instrument=cls.drums,
            performance_readiness=PerformanceReadiness.READY,
        )

    def test_recommendations_are_grouped_by_part_coverage(self):
        response = self.client.get(reverse('band:gig_part_assignments_detail', kwargs={"slug": PRSB_SLUG, 'pk': self.gig.pk}))
        content = response.content.decode()

        self.assertContains(response, "All Parts Covered")
        self.assertContains(response, "Missing Parts")
        self.assertLess(content.index("All Parts Covered"), content.index("Missing Parts"))
        self.assertLess(content.index("Covered Song"), content.index("Missing Parts"))
        self.assertLess(content.index("Missing Parts"), content.index("Incomplete Song"))

    def test_group_heading_renders_once_per_group(self):
        response = self.client.get(reverse('band:gig_part_assignments_detail', kwargs={"slug": PRSB_SLUG, 'pk': self.gig.pk}))
        content = response.content.decode()

        self.assertEqual(content.count("All Parts Covered"), 1)
        self.assertEqual(content.count("Missing Parts"), 1)


class GigPartAssignmentOverrideTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Test Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.song,
            in_gig_rotation=True,
        )
        cls.part_a = SongPart.objects.create(song=cls.song, name="Part A")
        cls.part_b = SongPart.objects.create(song=cls.song, name="Part B")

        cls.drums = Instrument.objects.create(name="Drums", order=0)
        cls.guitar = Instrument.objects.create(name="Guitar", order=1)

        cls.member_a = User.objects.create_user(username="alice", first_name="Alice", last_name="Smith")
        cls.member_b = User.objects.create_user(username="bob", first_name="Bob", last_name="Jones")
        prsb = Band.objects.get(slug="prsb")
        for user in (cls.member_a, cls.member_b):
            BandMembership.objects.get_or_create(
                band=prsb, member=user.bandmember, defaults={"is_active": True},
            )

        cls.gig = Gig.objects.create(
            name="Test Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        _link_gig_to_prsb(cls.gig)
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
            reverse('band:gig_part_assignment_override_delete', kwargs={"slug": PRSB_SLUG, 'pk': self.gig.pk, 'override_id': override.pk}),
        )

        self.assertRedirects(response, reverse('band:gig_part_assignments_detail', kwargs={"slug": PRSB_SLUG, 'pk': self.gig.pk}))
        self.assertFalse(GigPartAssignmentOverride.objects.filter(pk=override.pk).exists())


class PartAssignmentOrderingTestCase(TestCase):
    """Rows for a song read part order, then instrument order, then player name."""

    @classmethod
    def setUpTestData(cls):
        cls.low = Instrument.objects.create(name="Ordering Low", quantity=2, order=0)
        cls.high = Instrument.objects.create(name="Ordering High", quantity=2, order=1)

        cls.alice = User.objects.create_user(username="ord_alice", first_name="Alice", last_name="A")
        cls.bob = User.objects.create_user(username="ord_bob", first_name="Bob", last_name="B")
        cls.carol = User.objects.create_user(username="ord_carol", first_name="Carol", last_name="C")
        cls.zed = User.objects.create_user(username="ord_zed", first_name="Zed", last_name="Z")
        prsb = Band.objects.get(slug="prsb")
        for user in (cls.alice, cls.bob, cls.carol, cls.zed):
            BandMembership.objects.create(band=prsb, member=user.bandmember)

    def test_rows_sorted_by_part_then_instrument_then_name(self):
        song = Song.objects.create(title="Ordering Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=song,
            in_gig_rotation=True,
        )
        first = SongPart.objects.create(song=song, name="First Part")
        second = SongPart.objects.create(song=song, name="Second Part")

        PartAssignment.objects.create(member=self.bob.bandmember, song_part=first, instrument=self.high)
        PartAssignment.objects.create(member=self.zed.bandmember, song_part=first, instrument=self.high)
        PartAssignment.objects.create(member=self.carol.bandmember, song_part=first, instrument=self.low)
        PartAssignment.objects.create(member=self.alice.bandmember, song_part=second, instrument=self.low)

        gig = Gig.objects.create(
            name="Ordering Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        _link_gig_to_prsb(gig)
        GigInstrument.objects.create(gig=gig, instrument=self.low, gig_quantity=2)
        GigInstrument.objects.create(gig=gig, instrument=self.high, gig_quantity=2)
        for member in BandMember.objects.filter(user__username__startswith="ord_"):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
        GigSetlistEntry.objects.create(gig=gig, song=song)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        rows = [
            (pa.song_part.name, pa.instrument.name, pa.member.user.get_full_name())
            for pa in setlist[0].part_assignments
        ]
        self.assertEqual(rows, [
            ("First Part", "Ordering Low", "Carol C"),
            ("First Part", "Ordering High", "Bob B"),
            ("First Part", "Ordering High", "Zed Z"),
            ("Second Part", "Ordering Low", "Alice A"),
        ])


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


class GigIncludeInStatsTestCase(TestCase):
    def test_default_is_included(self):
        gig = Gig.objects.create(
            name="Full Band Gig",
            start_datetime=timezone.now() - timedelta(days=1),
            end_datetime=timezone.now() - timedelta(days=1) + timedelta(hours=2),
        )
        self.assertTrue(gig.include_in_stats)

    def test_can_leave_a_gig_out_of_stats(self):
        gig = Gig.objects.create(
            name="Small Gig",
            start_datetime=timezone.now() - timedelta(days=1),
            end_datetime=timezone.now() - timedelta(days=1) + timedelta(hours=2),
            include_in_stats=False,
        )
        self.assertFalse(Gig.objects.get(pk=gig.pk).include_in_stats)


class CoverageRiskScoringTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.prsb = Band.objects.get(slug="prsb")
        cls.lead = Instrument.objects.create(name="Lead", order=0)
        cls.bass = Instrument.objects.create(name="Bass", order=1)

        cls.alice_user = User.objects.create_user(username="cr_alice", first_name="Alice", last_name="A")
        cls.bob_user = User.objects.create_user(username="cr_bob", first_name="Bob", last_name="B")
        cls.cara_user = User.objects.create_user(username="cr_cara", first_name="Cara", last_name="C")
        cls.alice = BandMember.objects.get(user=cls.alice_user)
        cls.bob = BandMember.objects.get(user=cls.bob_user)
        cls.cara = BandMember.objects.get(user=cls.cara_user)
        for member in (cls.alice, cls.bob, cls.cara):
            BandMembership.objects.create(band=cls.prsb, member=member)

        cls.song = Song.objects.create(title="Yellow Bird")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.song,
            in_gig_rotation=True,
        )
        cls.other = Song.objects.create(title="Not In Rotation")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.other,
            in_gig_rotation=False,
        )
        cls.part_lead = SongPart.objects.create(song=cls.song, name="Lead")
        cls.part_bass = SongPart.objects.create(song=cls.song, name="Bass")
        SongPart.objects.create(song=cls.other, name="Lead")

    def _past_gig(self, days_ago, *, include_in_stats=True, name=None):
        start = self.now - timedelta(days=days_ago)
        return _link_gig_to_prsb(
            Gig.objects.create(
                name=name or f"Gig-{days_ago}",
                start_datetime=start,
                end_datetime=start + timedelta(hours=2),
                include_in_stats=include_in_stats,
            )
        )

    def test_zero_gigs_attendance_rate_is_one(self):
        PartAssignment.objects.create(
            member=self.alice, song_part=self.part_lead, instrument=self.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        report = get_coverage_risk(self.prsb, lookback=10)
        self.assertEqual(report.lookback_gigs_used, 0)
        song = report.songs[0]
        lead = next(p for p in song.parts if p.song_part.id == self.part_lead.id)
        self.assertAlmostEqual(lead.effective_coverage, 1.0)
        self.assertAlmostEqual(lead.coverers[0].attendance_rate, 1.0)

    def test_small_group_and_future_gigs_excluded(self):
        full = self._past_gig(2, name="Full")
        self._past_gig(1, include_in_stats=False, name="Small")
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
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
        report = get_coverage_risk(self.prsb, lookback=10)
        lead = next(
            p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id
        )
        self.assertAlmostEqual(lead.effective_coverage, 1.0)
        self.assertEqual(len(lead.coverers), 1)
        self.assertEqual(lead.coverers[0].member.user.username, "cr_alice")

    def test_coverage_risk_ignores_coverers_outside_the_active_roster(self):
        other = Band.objects.create(name="Cover Other", slug="cover-other")
        outsider = User.objects.create_user(
            username="cr_outside_roster", first_name="Out", last_name="Roster",
        )
        inactive = User.objects.create_user(
            username="cr_inactive_roster", first_name="Inact", last_name="Roster",
        )
        BandMembership.objects.create(band=other, member=outsider.bandmember, is_active=True)
        BandMembership.objects.create(band=self.prsb, member=inactive.bandmember, is_active=False)
        for member in (outsider.bandmember, inactive.bandmember):
            PartAssignment.objects.create(
                member=member, song_part=self.part_lead, instrument=self.lead,
                performance_readiness=PerformanceReadiness.READY,
            )
        report = get_coverage_risk(self.prsb, lookback=10)
        lead = next(p for p in report.songs[0].parts if p.song_part.id == self.part_lead.id)
        names = {c.member.user.username for c in lead.coverers}
        self.assertNotIn("cr_outside_roster", names)
        self.assertNotIn("cr_inactive_roster", names)
        self.assertAlmostEqual(lead.effective_coverage, 0.0)


class BandReportScopeTestCase(TestCase):
    def test_coverage_risk_ignores_another_bands_rotation_song(self):
        other = Band.objects.create(name="Other", slug="other-report")
        song = Song.objects.create(title="Other Rotation")
        SongPart.objects.create(song=song, name="Lead")
        BandSong.objects.create(band=other, song=song, in_gig_rotation=True)
        report = get_coverage_risk(Band.objects.get(slug="prsb"))
        self.assertNotIn("Other Rotation", [row.song.title for row in report.songs])

    def test_dietary_gig_outside_the_band_falls_back_to_roster(self):
        other = Band.objects.create(name="Diet Other", slug="diet-other")
        gig = Gig.objects.create(
            name="Other Gig",
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=2),
        )
        gig.bands.add(other)
        resolved = resolve_gig(str(gig.pk), band=Band.objects.get(slug="prsb"))
        self.assertIsNone(resolved)


class CoverageRiskViewsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="viewer", password="pass")
        rotation_song = Song.objects.create(title="Rotation Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=rotation_song,
            in_gig_rotation=True,
        )

    def test_reports_hub_ok(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:reports", kwargs={"slug": PRSB_SLUG}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Coverage Risk")

    def test_coverage_risk_ok_default_n(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:coverage_risk", kwargs={"slug": PRSB_SLUG}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["lookback"], 10)

    def test_coverage_risk_n_query_param(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:coverage_risk", kwargs={"slug": PRSB_SLUG}), {"n": "5"})
        self.assertEqual(resp.context["lookback"], 5)

    def test_coverage_risk_invalid_n_defaults(self):
        self.client.login(username="viewer", password="pass")
        for bad in ["0", "-3", "abc"]:
            resp = self.client.get(reverse("band:coverage_risk", kwargs={"slug": PRSB_SLUG}), {"n": bad})
            self.assertEqual(resp.context["lookback"], 10, msg=bad)

    def test_coverage_risk_huge_n_clamped(self):
        self.client.login(username="viewer", password="pass")
        resp = self.client.get(reverse("band:coverage_risk", kwargs={"slug": PRSB_SLUG}), {"n": "999999"})
        self.assertEqual(resp.context["lookback"], 200)


class GigListPastPaginationTestCase(TestCase):
    def _past_gig(self, days_ago, name=None):
        start = timezone.now() - timedelta(days=days_ago)
        return _link_gig_to_prsb(
            Gig.objects.create(
                name=name or f"Past-{days_ago}",
                start_datetime=start,
                end_datetime=start + timedelta(hours=2),
            )
        )

    def _upcoming_gig(self, days_ahead, name=None):
        start = timezone.now() + timedelta(days=days_ahead)
        return _link_gig_to_prsb(
            Gig.objects.create(
                name=name or f"Upcoming-{days_ahead}",
                start_datetime=start,
                end_datetime=start + timedelta(hours=2),
            )
        )

    def test_first_page_has_at_most_ten_past_gigs(self):
        for i in range(1, 16):
            self._past_gig(i, name=f"P{i}")
        self._upcoming_gig(1, name="U1")

        resp = self.client.get(reverse("band:gig_list", kwargs={"slug": PRSB_SLUG}))
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

        resp = self.client.get(reverse("band:gig_list", kwargs={"slug": PRSB_SLUG}), {"page": "2"})
        self.assertEqual(resp.status_code, 200)
        past = list(resp.context["past_gigs"])
        self.assertEqual([g.name for g in past], [f"P{i}" for i in range(11, 16)])
        self.assertEqual(resp.context["page_obj"].number, 2)

    def test_invalid_page_still_returns_200(self):
        for i in range(1, 12):
            self._past_gig(i, name=f"P{i}")

        for bad in ("999", "abc", "-1", "0"):
            with self.subTest(page=bad):
                resp = self.client.get(reverse("band:gig_list", kwargs={"slug": PRSB_SLUG}), {"page": bad})
                self.assertEqual(resp.status_code, 200)
                self.assertIn("page_obj", resp.context)
                self.assertTrue(1 <= resp.context["page_obj"].number <= 2)

    def test_pagination_controls_when_multiple_pages(self):
        for i in range(1, 12):
            self._past_gig(i, name=f"P{i}")

        resp = self.client.get(reverse("band:gig_list", kwargs={"slug": PRSB_SLUG}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Previous", count=0)  # page 1: no Previous
        self.assertContains(resp, "Next")
        self.assertContains(resp, 'href="?page=2"')
        # Current page is not a link to itself as ?page=1 in a way that duplicates;
        # at minimum page 2 link exists and "1" appears as current.
        self.assertContains(resp, "<strong>1</strong>")

        resp2 = self.client.get(reverse("band:gig_list", kwargs={"slug": PRSB_SLUG}), {"page": "2"})
        self.assertContains(resp2, "Previous")
        self.assertContains(resp2, 'href="?page=1"')
        self.assertContains(resp2, "Next", count=0)
        self.assertContains(resp2, "<strong>2</strong>")

    def test_no_pagination_controls_when_single_page(self):
        for i in range(1, 6):
            self._past_gig(i, name=f"P{i}")

        resp = self.client.get(reverse("band:gig_list", kwargs={"slug": PRSB_SLUG}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Previous")
        self.assertNotContains(resp, "Next")
        self.assertNotContains(resp, 'href="?page=')


class SoloFieldsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Solo Fields Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.song,
            in_gig_rotation=True,
        )
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
        cls.song = Song.objects.create(title="Form Solo Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.song,
            in_gig_rotation=True,
        )
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
        cls.band = Band.objects.create(name="Kit Cover Band", slug="kit-cover-band")
        cls.user = User.objects.create_user(
            username="kit_cover_model_user", first_name="Kit", last_name="Cover",
        )

    def test_instrument_flag_default_false(self):
        inst = Instrument.objects.create(name="KitCoverModel Default", order=2)
        self.assertFalse(inst.is_drum_kit_cover_instrument)

    def test_drum_kit_cover_player_unique_member_and_str(self):
        row = DrumKitCoverPlayer.objects.create(
            band=self.band, member=self.user.bandmember, priority=1,
        )
        self.assertEqual(str(row), "Kit Cover (priority 1)")
        dup = DrumKitCoverPlayer(band=self.band, member=self.user.bandmember, priority=2)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                dup.save()


class DrumKitCoverInjectionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.band = Band.objects.create(name="Kit Inject Band", slug="kit-inject-band")
        cls.song = Song.objects.create(title="Kit Inject Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.song,
            in_gig_rotation=True,
        )
        cls.engine = SongPart.objects.create(song=cls.song, name="Engine Room")
        cls.lead = SongPart.objects.create(song=cls.song, name="Lead")
        cls.kit = Instrument.objects.create(
            name="KitInject Drum Set", order=0, is_drum_kit_cover_instrument=True,
        )
        cls.lead_inst = Instrument.objects.create(name="KitInject Lead", order=1)

        cls.ben = User.objects.create_user(username="kit_ben", first_name="Ben", last_name="K")
        cls.jansen = User.objects.create_user(username="kit_jansen", first_name="Jansen", last_name="K")
        cls.carlos = User.objects.create_user(username="kit_carlos", first_name="Carlos", last_name="K")
        cls.dario = User.objects.create_user(username="kit_dario", first_name="Dario", last_name="K")

        PartAssignment.objects.create(
            member=cls.ben.bandmember, song_part=cls.engine, instrument=cls.kit,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.jansen.bandmember, song_part=cls.lead, instrument=cls.lead_inst,
            performance_readiness=PerformanceReadiness.READY,
        )

        DrumKitCoverPlayer.objects.create(band=cls.band, member=cls.jansen.bandmember, priority=1)
        DrumKitCoverPlayer.objects.create(band=cls.band, member=cls.ben.bandmember, priority=2)
        DrumKitCoverPlayer.objects.create(band=cls.band, member=cls.carlos.bandmember, priority=2)
        DrumKitCoverPlayer.objects.create(band=cls.band, member=cls.dario.bandmember, priority=2)

        cls.kit_repertoire = list(
            PartAssignment.objects.filter(song_part__song=cls.song, instrument=cls.kit)
        )
        cls.covers = list(DrumKitCoverPlayer.objects.all())

    def _inject(self, available, overrides=None):
        return inject_drum_kit_cover_assignments(
            song=self.song,
            kit_instrument=self.kit,
            available_members=set(available),
            song_overrides=overrides or [],
            cover_players=self.covers,
            kit_repertoire=self.kit_repertoire,
        )

    def test_no_inject_when_listed_player_available(self):
        result = self._inject([self.ben.bandmember, self.jansen.bandmember, self.carlos.bandmember])
        self.assertEqual(result, [])

    def test_injects_best_priority_when_listed_absent(self):
        result = self._inject([self.jansen.bandmember, self.carlos.bandmember])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].member, self.jansen.bandmember)
        self.assertEqual(result[0].song_part, self.engine)
        self.assertEqual(result[0].instrument, self.kit)
        self.assertEqual(result[0].performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(result[0].pk)  # unsaved
        self.assertTrue(result[0].is_drum_kit_cover)

    def test_not_playing_falls_through_to_next_tier(self):
        gig = Gig.objects.create(
            name="Kit Inject Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
        )
        gi = GigInstrument.objects.create(gig=gig, instrument=self.kit, gig_quantity=1)
        override = GigPartAssignmentOverride(
            member=self.jansen.bandmember,
            song_part=self.engine,
            gig_instrument=gi,
            override_type=OverrideType.NOT_PLAYING,
        )
        result = self._inject(
            [self.jansen.bandmember, self.carlos.bandmember],
            overrides=[override],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].member, self.carlos.bandmember)

    def test_maybe_available_listed_player_does_not_block_covers(self):
        # Ben not in available_members (maybe/unavailable) → covers inject
        result = self._inject([self.jansen.bandmember])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].member, self.jansen.bandmember)

    def test_no_kit_instrument_returns_empty(self):
        result = inject_drum_kit_cover_assignments(
            song=self.song,
            kit_instrument=None,
            available_members={self.jansen.bandmember},
            song_overrides=[],
            cover_players=self.covers,
            kit_repertoire=self.kit_repertoire,
        )
        self.assertEqual(result, [])

    def test_no_kit_repertoire_returns_empty(self):
        result = inject_drum_kit_cover_assignments(
            song=self.song,
            kit_instrument=self.kit,
            available_members={self.jansen.bandmember},
            song_overrides=[],
            cover_players=self.covers,
            kit_repertoire=[],
        )
        self.assertEqual(result, [])

    def test_returns_all_eligible_covers_at_best_priority(self):
        result = self._inject([self.carlos.bandmember, self.dario.bandmember])
        self.assertEqual(
            {assignment.member for assignment in result},
            {self.carlos.bandmember, self.dario.bandmember},
        )

    def test_skip_cover_with_assign_override_on_song(self):
        gig = Gig.objects.create(
            name="Kit Assign Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
        )
        gi_lead = GigInstrument.objects.create(gig=gig, instrument=self.lead_inst, gig_quantity=1)
        override = GigPartAssignmentOverride(
            member=self.jansen.bandmember,
            song_part=self.lead,
            gig_instrument=gi_lead,
            override_type=OverrideType.ASSIGN,
            performance_readiness=PerformanceReadiness.READY,
        )
        result = self._inject(
            [self.jansen.bandmember, self.carlos.bandmember],
            overrides=[override],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].member, self.carlos.bandmember)


class DrumKitCoverGigAssignmentTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.song = Song.objects.create(title="Kit Gig Song")
        BandSong.objects.create(
            band=Band.objects.get(slug="prsb"),
            song=cls.song,
            in_gig_rotation=True,
        )
        cls.engine = SongPart.objects.create(song=cls.song, name="Engine Room")
        cls.lead_part = SongPart.objects.create(song=cls.song, name="Lead")

        cls.kit = Instrument.objects.create(
            name="KitGig Drum Set", order=0, is_drum_kit_cover_instrument=True,
            include_in_gig_song_count=True,
        )
        cls.lead = Instrument.objects.create(name="KitGig Lead", order=1)

        cls.ben = User.objects.create_user(username="kitgig_ben", first_name="Ben", last_name="G")
        cls.jansen = User.objects.create_user(username="kitgig_jansen", first_name="Jansen", last_name="G")
        cls.carlos = User.objects.create_user(username="kitgig_carlos", first_name="Carlos", last_name="G")
        cls.lead_player = User.objects.create_user(username="kitgig_lead", first_name="Lead", last_name="G")

        PartAssignment.objects.create(
            member=cls.ben.bandmember, song_part=cls.engine, instrument=cls.kit,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.jansen.bandmember, song_part=cls.lead_part, instrument=cls.lead,
            performance_readiness=PerformanceReadiness.READY,
        )
        PartAssignment.objects.create(
            member=cls.lead_player.bandmember, song_part=cls.lead_part, instrument=cls.lead,
            performance_readiness=PerformanceReadiness.READY,
        )

        prsb = Band.objects.get(slug="prsb")
        for user in (cls.ben, cls.jansen, cls.carlos, cls.lead_player):
            BandMembership.objects.create(band=prsb, member=user.bandmember)
        DrumKitCoverPlayer.objects.create(band=prsb, member=cls.jansen.bandmember, priority=1)
        DrumKitCoverPlayer.objects.create(band=prsb, member=cls.carlos.bandmember, priority=2)

        cls.gig = Gig.objects.create(
            name="Kit Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        _link_gig_to_prsb(cls.gig)
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.kit, gig_quantity=1)
        GigInstrument.objects.create(gig=cls.gig, instrument=cls.lead, gig_quantity=2)

        for user in (cls.jansen, cls.carlos, cls.lead_player):
            GigAttendance.objects.create(
                gig=cls.gig, member=user.bandmember, status=GigAttendance.AVAILABLE,
            )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.ben.bandmember, status=GigAttendance.UNAVAILABLE,
        )
        GigSetlistEntry.objects.create(gig=cls.gig, song=cls.song)

    def test_jansen_covers_kit_when_ben_unavailable(self):
        setlist, _, _ = get_gig_part_assignments(self.gig, [])
        self.assertEqual(len(setlist), 1)
        kit_players = [
            pa for pa in setlist[0].part_assignments if pa.instrument_id == self.kit.id
        ]
        self.assertEqual(len(kit_players), 1)
        self.assertEqual(kit_players[0].member, self.jansen.bandmember)
        self.assertTrue(kit_players[0].is_drum_kit_cover)
        self.assertEqual(len(setlist[0].unplayed_parts), 0)

    def test_listed_ben_used_when_available(self):
        GigAttendance.objects.filter(gig=self.gig, member=self.ben.bandmember).update(
            status=GigAttendance.AVAILABLE,
        )
        setlist, _, _ = get_gig_part_assignments(self.gig, [])
        kit_players = [
            pa for pa in setlist[0].part_assignments if pa.instrument_id == self.kit.id
        ]
        self.assertEqual(len(kit_players), 1)
        self.assertEqual(kit_players[0].member, self.ben.bandmember)
        self.assertFalse(getattr(kit_players[0], "is_drum_kit_cover", False))

    def test_gig_part_assignments_mark_cover_member(self):
        response = self.client.get(
            reverse("band:gig_part_assignments_detail", kwargs={"slug": PRSB_SLUG, "pk": self.gig.pk})
        )
        self.assertContains(
            response,
            'Jansen G <span class="cover-marker" title="Drum kit cover">(cover)</span>',
            html=False,
        )

    def test_listed_player_assigned_to_lead_does_not_block_cover(self):
        GigAttendance.objects.filter(gig=self.gig, member=self.ben.bandmember).update(
            status=GigAttendance.AVAILABLE,
        )
        lead_gig_instrument = GigInstrument.objects.get(gig=self.gig, instrument=self.lead)
        override = GigPartAssignmentOverride(
            member=self.ben.bandmember,
            song_part=self.lead_part,
            gig_instrument=lead_gig_instrument,
            override_type=OverrideType.ASSIGN,
            performance_readiness=PerformanceReadiness.READY,
        )

        setlist, _, _ = get_gig_part_assignments(self.gig, [override])

        kit_players = [
            pa for pa in setlist[0].part_assignments if pa.instrument_id == self.kit.id
        ]
        self.assertEqual(len(kit_players), 1)
        self.assertEqual(kit_players[0].member, self.jansen.bandmember)
        self.assertEqual(len(setlist[0].unplayed_parts), 0)

    def test_gig_without_flagged_kit_instrument_does_not_inject_cover(self):
        GigInstrument.objects.filter(gig=self.gig, instrument=self.kit).delete()

        setlist, _, _ = get_gig_part_assignments(self.gig, [])

        self.assertEqual(len(setlist), 1)
        self.assertFalse(
            any(pa.instrument_id == self.kit.id for pa in setlist[0].part_assignments)
        )
        self.assertEqual(setlist[0].unplayed_parts, [self.engine])


class DietaryRestrictionQueryTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prsb = Band.objects.get(slug="prsb")

        def member(username, first, last, diet, active=True):
            user = User.objects.create_user(
                username=username, first_name=first, last_name=last, is_active=active,
            )
            bm = user.bandmember
            bm.dietary_restrictions = diet
            bm.save()
            BandMembership.objects.create(band=cls.prsb, member=bm)
            return bm

        cls.alice = member("dr_alice", "Alice", "Diet", "Vegetarian")
        cls.bob = member("dr_bob", "Bob", "Diet", "Peanut allergy")
        cls.cara = member("dr_cara", "Cara", "Diet", "   ")
        cls.dana = member("dr_dana", "Dana", "Diet", "")
        cls.erin = member("dr_erin", "Erin", "Diet", "Gluten free", active=False)
        cls.finn = member("dr_finn", "Finn", "Diet", "No shellfish")

        now = timezone.now()
        cls.gig = _link_gig_to_prsb(
            Gig.objects.create(
                name="Harvest Festival",
                start_datetime=now + timedelta(days=10),
                end_datetime=now + timedelta(days=10, hours=2),
            )
        )
        cls.past_gig = _link_gig_to_prsb(
            Gig.objects.create(
                name="Oak Tree Picnic",
                start_datetime=now - timedelta(days=20),
                end_datetime=now - timedelta(days=20) + timedelta(hours=2),
            )
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.alice, status=GigAttendance.AVAILABLE,
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.bob, status=GigAttendance.MAYBE_AVAILABLE,
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.finn, status=GigAttendance.UNAVAILABLE,
        )

    def test_default_excludes_blank_whitespace_inactive(self):
        names = [
            m.user.first_name
            for m in dietary_restriction_members(gig=None, statuses=None, band=self.prsb)
        ]
        self.assertEqual(names, ["Alice", "Bob", "Finn"])

    def test_resolve_gig_invalid(self):
        self.assertIsNone(resolve_gig(None, band=self.prsb))
        self.assertIsNone(resolve_gig("", band=self.prsb))
        self.assertIsNone(resolve_gig("abc", band=self.prsb))
        self.assertIsNone(resolve_gig("99999", band=self.prsb))
        self.assertIsNone(resolve_gig("99999999999999999999", band=self.prsb))
        self.assertEqual(resolve_gig(str(self.gig.pk), band=self.prsb), self.gig)

    def test_resolve_statuses_ignored_without_gig(self):
        self.assertIsNone(resolve_statuses(
            gig=None, status_key_present=True, raw_values=["no_status"],
        ))

    def test_resolve_statuses_default_available(self):
        self.assertEqual(
            resolve_statuses(gig=self.gig, status_key_present=False, raw_values=[]),
            [GigAttendance.AVAILABLE],
        )

    def test_resolve_statuses_explicit_empty(self):
        self.assertEqual(
            resolve_statuses(gig=self.gig, status_key_present=True, raw_values=[""]),
            [],
        )
        self.assertEqual(
            resolve_statuses(
                gig=self.gig, status_key_present=True, raw_values=["unavailable"],
            ),
            [],
        )

    def test_resolve_statuses_union_preserves_allowed_order(self):
        self.assertEqual(
            resolve_statuses(
                gig=self.gig,
                status_key_present=True,
                raw_values=["no_status", "available", "no_status", "bogus"],
            ),
            [STATUS_NO_STATUS, GigAttendance.AVAILABLE],
        )

    def test_gig_available_default(self):
        statuses = resolve_statuses(gig=self.gig, status_key_present=False, raw_values=[])
        names = [
            m.user.first_name
            for m in dietary_restriction_members(gig=self.gig, statuses=statuses, band=self.prsb)
        ]
        self.assertEqual(names, ["Alice"])

    def test_gig_available_and_no_status(self):
        members = dietary_restriction_members(
            gig=self.gig,
            statuses=[GigAttendance.AVAILABLE, STATUS_NO_STATUS],
            band=self.prsb,
        )
        names = [m.user.first_name for m in members]
        self.assertEqual(names, ["Alice"])

    def test_gig_maybe_and_no_status_includes_cara_gap(self):
        # Cara has whitespace-only diet so she is never in the base set.
        # Dana has empty diet. No extra member has a diet and no attendance row
        # except we need one: create in this test.
        user = User.objects.create_user(username="dr_gabe", first_name="Gabe", last_name="Diet")
        gabe = user.bandmember
        gabe.dietary_restrictions = "Kosher"
        gabe.save()
        BandMembership.objects.create(band=self.prsb, member=gabe)
        members = dietary_restriction_members(
            gig=self.gig,
            statuses=[GigAttendance.MAYBE_AVAILABLE, STATUS_NO_STATUS],
            band=self.prsb,
        )
        names = [m.user.first_name for m in members]
        self.assertEqual(names, ["Bob", "Gabe"])

    def test_unavailable_never_included(self):
        members = dietary_restriction_members(
            gig=self.gig,
            statuses=[GigAttendance.AVAILABLE, GigAttendance.MAYBE_AVAILABLE, STATUS_NO_STATUS],
            band=self.prsb,
        )
        names = [m.user.first_name for m in members]
        self.assertNotIn("Finn", names)

    def test_empty_statuses_yields_no_members(self):
        self.assertEqual(
            list(dietary_restriction_members(gig=self.gig, statuses=[], band=self.prsb)),
            [],
        )

    def test_unrecognized_statuses_yields_no_members(self):
        self.assertEqual(
            list(dietary_restriction_members(gig=self.gig, statuses=["unavailable"], band=self.prsb)),
            [],
        )

    def test_status_caption_labels(self):
        self.assertEqual(
            status_caption_labels(
                [GigAttendance.MAYBE_AVAILABLE, STATUS_NO_STATUS, "bogus"],
            ),
            ["Maybe available", "No status yet"],
        )
        self.assertEqual(status_caption_labels([]), [])

    def test_upcoming_gigs_and_picker_rows(self):
        upcoming = list(upcoming_gigs(self.prsb))
        self.assertEqual(upcoming, [self.gig])
        rows = gig_picker_rows(Gig.objects.all())
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[self.gig.pk]["name"], "Harvest Festival")
        self.assertRegex(by_id[self.gig.pk]["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_past_gigs_excludes_upcoming(self):
        self.assertEqual(list(past_gigs(self.prsb)), [self.past_gig])


class DietaryRestrictionsViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prsb = Band.objects.get(slug="prsb")
        cls.viewer = User.objects.create_user(username="dr_viewer", password="pass")
        alice_user = User.objects.create_user(username="dr_view_alice", first_name="Alice", last_name="View")
        bob_user = User.objects.create_user(username="dr_view_bob", first_name="Bob", last_name="View")
        finn_user = User.objects.create_user(username="dr_view_finn", first_name="Finn", last_name="View")
        gabe_user = User.objects.create_user(username="dr_view_gabe", first_name="Gabe", last_name="View")
        cls.alice = alice_user.bandmember
        cls.alice.dietary_restrictions = "Vegetarian, no mushrooms"
        cls.alice.save()
        cls.bob = bob_user.bandmember
        cls.bob.dietary_restrictions = "Peanut allergy"
        cls.bob.save()
        cls.finn = finn_user.bandmember
        cls.finn.dietary_restrictions = "No shellfish"
        cls.finn.save()
        cls.gabe = gabe_user.bandmember
        cls.gabe.dietary_restrictions = "Kosher"
        cls.gabe.save()
        for member in (cls.alice, cls.bob, cls.finn, cls.gabe):
            BandMembership.objects.create(band=cls.prsb, member=member)
        now = timezone.now()
        cls.gig = _link_gig_to_prsb(
            Gig.objects.create(
                name="Harvest Festival",
                start_datetime=now + timedelta(days=5),
                end_datetime=now + timedelta(days=5, hours=2),
            )
        )
        cls.past_gig = _link_gig_to_prsb(
            Gig.objects.create(
                name="Oak Tree Picnic",
                start_datetime=now - timedelta(days=30),
                end_datetime=now - timedelta(days=30) + timedelta(hours=2),
            )
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.alice, status=GigAttendance.AVAILABLE,
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.bob, status=GigAttendance.MAYBE_AVAILABLE,
        )
        GigAttendance.objects.create(
            gig=cls.gig, member=cls.finn, status=GigAttendance.UNAVAILABLE,
        )

    def _get(self, **params):
        self.client.login(username="dr_viewer", password="pass")
        return self.client.get(reverse("band:dietary_restrictions", kwargs={"slug": PRSB_SLUG}), params)

    def test_anonymous_redirects_to_login(self):
        resp = self.client.get(reverse("band:dietary_restrictions", kwargs={"slug": PRSB_SLUG}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_signed_in_ok(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Vegetarian, no mushrooms")
        self.assertContains(resp, "Bob View")
        self.assertContains(resp, "Peanut allergy")
        self.assertContains(resp, "Gabe View")
        self.assertContains(resp, "Kosher")

    def test_gig_picker_is_a_grouped_select(self):
        resp = self._get()
        self.assertContains(resp, '<select name="gig" id="gig-select">')
        self.assertContains(resp, '<optgroup label="Upcoming">')
        self.assertContains(resp, '<optgroup label="Past">')
        self.assertContains(resp, f'<option value="{self.gig.pk}" >Harvest Festival')
        self.assertContains(resp, f'<option value="{self.past_gig.pk}" >Oak Tree Picnic')
        self.assertNotContains(resp, 'type="radio"')

    def test_selected_gig_is_selected_in_the_picker(self):
        resp = self._get(gig=str(self.past_gig.pk))
        self.assertContains(resp, f'<option value="{self.past_gig.pk}" selected>Oak Tree Picnic')

    def test_gig_without_status_param_defaults_available(self):
        resp = self._get(gig=str(self.gig.pk))
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Vegetarian, no mushrooms")
        self.assertNotContains(resp, "Bob View")
        self.assertNotContains(resp, "Gabe View")
        self.assertNotContains(resp, "Finn View")

    def test_gig_available_and_no_status(self):
        resp = self._get(gig=str(self.gig.pk), status=["available", "no_status"])
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Gabe View")
        self.assertNotContains(resp, "Bob View")

    def test_explicit_empty_status_is_empty_table(self):
        resp = self._get(gig=str(self.gig.pk), status=[""])
        self.assertNotContains(resp, "Alice View")
        self.assertContains(resp, "No members match")

    def test_junk_status_only_is_empty_table(self):
        resp = self._get(gig=str(self.gig.pk), status=["unavailable"])
        self.assertNotContains(resp, "Alice View")
        self.assertContains(resp, "No members match")

    def test_invalid_gig_is_unfiltered(self):
        resp = self._get(gig="abc", status=["no_status"])
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Bob View")
        self.assertContains(resp, "Gabe View")

    def test_status_without_gig_is_ignored(self):
        resp = self._get(status=["no_status"])
        self.assertContains(resp, "Alice View")
        self.assertContains(resp, "Bob View")

    def test_hub_links_to_report(self):
        self.client.login(username="dr_viewer", password="pass")
        resp = self.client.get(reverse("band:reports", kwargs={"slug": PRSB_SLUG}))
        self.assertContains(resp, reverse("band:dietary_restrictions", kwargs={"slug": PRSB_SLUG}))
        self.assertContains(resp, "Dietary Restrictions")

    def test_caption_uses_status_labels(self):
        resp = self._get(gig=str(self.gig.pk), status=["maybe_available", "no_status"])
        self.assertEqual(resp.context["status_labels"], ["Maybe available", "No status yet"])
        self.assertContains(resp, "Harvest Festival")
        self.assertContains(resp, "Maybe available, No status yet")


class BandModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="band_model_user", first_name="Ada", last_name="Brass")
        cls.song = Song.objects.create(title="Band Model Song")
        cls.gig = Gig.objects.create(
            name="Band Model Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )

    def test_membership_and_song_links_are_unique(self):
        band = Band.objects.create(name="River City", slug="river-city")
        BandMembership.objects.create(band=band, member=self.user.bandmember)
        BandSong.objects.create(band=band, song=self.song, in_gig_rotation=True)
        with self.assertRaises(Exception):
            BandMembership.objects.create(band=band, member=self.user.bandmember)
        with self.assertRaises(Exception):
            BandSong.objects.create(band=band, song=self.song)

    def test_band_delete_refused_while_named_by_gig(self):
        band = Band.objects.create(name="Host", slug="host")
        self.gig.bands.add(band)
        with self.assertRaises(ValidationError):
            band.delete()


class PrsbBackfillMigrationTestCase(TransactionTestCase):
    def _seed_historical_rows_at_0033(self):
        state = MigrationExecutor(connection).loader.project_state(
            [("band", "0033_seed_drum_kit_cover_players")],
        )
        apps = state.apps
        SongModel = apps.get_model("band", "Song")
        UserModel = apps.get_model("auth", "User")
        GigModel = apps.get_model("band", "Gig")
        BandMemberModel = apps.get_model("band", "BandMember")
        DrumKitCoverPlayerModel = apps.get_model("band", "DrumKitCoverPlayer")
        BandSpecialDateModel = apps.get_model("band", "BandSpecialDate")

        song = SongModel.objects.create(title="Backfill Song", composer="X", in_gig_rotation=True)
        user = UserModel.objects.create_user(username="backfill_user", first_name="Back", last_name="Fill")
        member = BandMemberModel.objects.create(user=user)
        GigModel.objects.create(
            name="Backfill Gig",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        cover = DrumKitCoverPlayerModel.objects.create(member=member, priority=1)
        special = BandSpecialDateModel.objects.create(
            date=YearlessDate(17, 10),
            description="Backfill special date",
        )
        return song.pk, cover.pk, special.pk

    def _remigrate_through_backfill(self, seed_historical_rows=False):
        call_command("migrate", "band", "0033", verbosity=0, interactive=False)
        seeded = None
        if seed_historical_rows:
            seeded = self._seed_historical_rows_at_0033()
        call_command("migrate", "band", "0034", verbosity=0, interactive=False)
        return seeded

    def test_prsb_band_exists_after_migrate(self):
        self._remigrate_through_backfill()
        band = Band.objects.get(slug="prsb")
        self.assertEqual(band.name, "PRSB")
        self.assertTrue(band.is_active)

    def test_historical_rows_attach_to_prsb(self):
        song_id, cover_id, special_id = self._remigrate_through_backfill(seed_historical_rows=True)
        band = Band.objects.get(slug="prsb")
        self.assertTrue(band.is_active)
        self.assertEqual(band.name, "PRSB")
        self.assertEqual(BandSong.objects.filter(band=band, is_active=True).count(), Song.objects.count())
        self.assertEqual(BandMembership.objects.filter(band=band, is_active=True).count(), BandMember.objects.count())
        self.assertFalse(Gig.objects.exclude(bands=band).exists())

        song = Song.objects.get(pk=song_id)
        band_song = BandSong.objects.get(band=band, song=song)
        self.assertTrue(band_song.in_gig_rotation)

        cover = DrumKitCoverPlayer.objects.get(pk=cover_id)
        self.assertEqual(cover.band, band)

        special = BandSpecialDate.objects.get(pk=special_id)
        self.assertEqual(special.band, band)

    def test_reverse_0035_copies_prsb_rotation_back_to_song(self):
        try:
            call_command("migrate", "band", "0033", verbosity=0, interactive=False)
            state = MigrationExecutor(connection).loader.project_state(
                [("band", "0033_seed_drum_kit_cover_players")],
            )
            SongModel = state.apps.get_model("band", "Song")
            on_song = SongModel.objects.create(title="Rotation On", composer="X", in_gig_rotation=True)
            off_song = SongModel.objects.create(title="Rotation Off", composer="X", in_gig_rotation=False)
            call_command("migrate", "band", "0035", verbosity=0, interactive=False)
            call_command("migrate", "band", "0034", verbosity=0, interactive=False)
            state_0034 = MigrationExecutor(connection).loader.project_state(
                [("band", "0034_multiple_bands")],
            )
            HistoricalSong = state_0034.apps.get_model("band", "Song")
            self.assertTrue(HistoricalSong.objects.get(pk=on_song.pk).in_gig_rotation)
            self.assertFalse(HistoricalSong.objects.get(pk=off_song.pk).in_gig_rotation)
        finally:
            call_command("migrate", "band", verbosity=0, interactive=False)

    def test_reverse_0034_does_not_delete_cover_players_or_special_dates(self):
        try:
            _, cover_id, special_id = self._remigrate_through_backfill(seed_historical_rows=True)
            call_command("migrate", "band", "0033", verbosity=0, interactive=False)
            state = MigrationExecutor(connection).loader.project_state(
                [("band", "0033_seed_drum_kit_cover_players")],
            )
            Cover = state.apps.get_model("band", "DrumKitCoverPlayer")
            Special = state.apps.get_model("band", "BandSpecialDate")
            self.assertTrue(Cover.objects.filter(pk=cover_id).exists())
            self.assertTrue(Special.objects.filter(pk=special_id).exists())
        finally:
            call_command("migrate", "band", verbosity=0, interactive=False)


class IncludeInStatsMigrationTestCase(TransactionTestCase):
    def test_migration_negates_the_old_flag_and_reverse_restores_it(self):
        start = timezone.now()
        end = start + timedelta(hours=2)
        try:
            call_command("migrate", "band", "0035", verbosity=0, interactive=False)
            before = MigrationExecutor(connection).loader.project_state(
                [("band", "0035_remove_song_in_gig_rotation")],
            )
            HistoricalGig = before.apps.get_model("band", "Gig")
            excluded = HistoricalGig.objects.create(
                name="Was small", start_datetime=start, end_datetime=end, is_small_group=True,
            )
            included = HistoricalGig.objects.create(
                name="Was full", start_datetime=start, end_datetime=end, is_small_group=False,
            )
            call_command("migrate", "band", "0036", verbosity=0, interactive=False)
            after = MigrationExecutor(connection).loader.project_state(
                [("band", "0036_gig_include_in_stats")],
            )
            MigratedGig = after.apps.get_model("band", "Gig")
            self.assertFalse(MigratedGig.objects.get(pk=excluded.pk).include_in_stats)
            self.assertTrue(MigratedGig.objects.get(pk=included.pk).include_in_stats)

            call_command("migrate", "band", "0035", verbosity=0, interactive=False)
            restored = MigrationExecutor(connection).loader.project_state(
                [("band", "0035_remove_song_in_gig_rotation")],
            )
            RestoredGig = restored.apps.get_model("band", "Gig")
            self.assertTrue(RestoredGig.objects.get(pk=excluded.pk).is_small_group)
            self.assertFalse(RestoredGig.objects.get(pk=included.pk).is_small_group)
        finally:
            call_command("migrate", "band", verbosity=0, interactive=False)


class SongAbsoluteUrlTestCase(TestCase):
    def test_get_absolute_url_uses_linked_band_slug(self):
        song = Song.objects.create(title="Absolute URL Song")
        band = Band.objects.create(name="Alpha Band", slug="alpha-band")
        BandSong.objects.create(band=band, song=song, is_active=True)
        url = song.get_absolute_url()
        self.assertIn(f"/bands/{band.slug}/", url)
        expected = reverse("band:song_detail", kwargs={"slug": band.slug, "pk": song.pk})
        self.assertEqual(url, expected)

    def test_get_absolute_url_prefers_active_band_ordered_by_name(self):
        song = Song.objects.create(title="Multi Band Song")
        zulu = Band.objects.create(name="Zulu Band", slug="zulu")
        alpha = Band.objects.create(name="Alpha Band", slug="alpha")
        BandSong.objects.create(band=zulu, song=song, is_active=False)
        BandSong.objects.create(band=alpha, song=song, is_active=True)
        url = song.get_absolute_url()
        self.assertEqual(
            url,
            reverse("band:song_detail", kwargs={"slug": alpha.slug, "pk": song.pk}),
        )

    def test_get_absolute_url_without_band_link_raises(self):
        song = Song.objects.create(title="Orphan Song")
        with self.assertRaises(ValueError):
            song.get_absolute_url()


def _nav_text(response):
    nav = response.content.decode().split("<nav>", 1)[1].split("</nav>", 1)[0]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", nav)).strip()
    return html.unescape(text)


class BandRoutingTestCase(TestCase):
    def test_home_lists_the_band_and_global_links(self):
        response = self.client.get(reverse("band:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("band:band_home", kwargs={"slug": "prsb"}))
        self.assertContains(response, reverse("band:instrument_list"))
        self.assertContains(response, reverse("admin:index"))
        band_home = self.client.get(reverse("band:band_home", kwargs={"slug": "prsb"}))
        self.assertNotContains(band_home, reverse("band:instrument_list"))
        self.assertNotContains(band_home, reverse("admin:index"))

    def test_inactive_band_is_hidden_but_url_opens(self):
        Band.objects.create(name="Old", slug="old", is_active=False)
        home = self.client.get(reverse("band:band_home", kwargs={"slug": "prsb"}))
        self.assertNotContains(home, reverse("band:band_home", kwargs={"slug": "old"}))
        opened = self.client.get(reverse("band:band_home", kwargs={"slug": "old"}))
        self.assertEqual(opened.status_code, 200)

    def test_unknown_band_is_404(self):
        response = self.client.get("/bands/missing/")
        self.assertEqual(response.status_code, 404)

    def test_band_name_is_a_breadcrumb_not_a_header_switcher(self):
        other = Band.objects.create(name="Guest Band", slug="guest")
        home = self.client.get(reverse("band:band_home", kwargs={"slug": "prsb"}))
        self.assertEqual(_nav_text(home), "Home > PRSB")
        self.assertNotContains(home, reverse("band:band_home", kwargs={"slug": "guest"}))

        gigs = self.client.get(reverse("band:gig_list", kwargs={"slug": other.slug}))
        self.assertEqual(_nav_text(gigs), "Home > Guest Band > Gigs")
        self.assertContains(gigs, reverse("band:home"))
        self.assertContains(gigs, reverse("band:band_home", kwargs={"slug": "guest"}))

    def test_old_gig_list_is_gone(self):
        response = self.client.get("/gigs/")
        self.assertEqual(response.status_code, 404)

    def test_gig_is_listed_under_every_band_on_it(self):
        other = Band.objects.create(name="Guest", slug="guest")
        gig = Gig.objects.create(
            name="Shared Gig",
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=2),
        )
        prsb = Band.objects.get(slug="prsb")
        gig.bands.add(prsb, other)
        for slug in ("prsb", "guest"):
            response = self.client.get(reverse("band:gig_list", kwargs={"slug": slug}))
            self.assertContains(response, "Shared Gig")
        outsider = Band.objects.create(name="Outsider", slug="outsider")
        missing = self.client.get(reverse("band:gig_detail", kwargs={"slug": outsider.slug, "pk": gig.pk}))
        self.assertEqual(missing.status_code, 404)

    def test_gig_detail_lists_every_band_on_the_gig(self):
        prsb = Band.objects.get(slug="prsb")
        solo = Gig.objects.create(
            name="Solo Gig",
            start_datetime=timezone.now() + timedelta(days=2),
            end_datetime=timezone.now() + timedelta(days=2, hours=2),
        )
        solo.bands.add(prsb)
        solo_page = self.client.get(reverse("band:gig_detail", kwargs={"slug": "prsb", "pk": solo.pk}))
        self.assertContains(solo_page, f"Bands:\n        \n            {prsb.name}")
        self.assertNotContains(solo_page, f'href="{reverse("band:gig_detail", kwargs={"slug": "prsb", "pk": solo.pk})}"')

        other = Band.objects.create(name="Guest", slug="guest")
        shared = Gig.objects.create(
            name="Shared Detail Gig",
            start_datetime=timezone.now() + timedelta(days=3),
            end_datetime=timezone.now() + timedelta(days=3, hours=2),
        )
        shared.bands.add(prsb, other)
        shared_page = self.client.get(reverse("band:gig_detail", kwargs={"slug": "prsb", "pk": shared.pk}))
        self.assertContains(shared_page, "Guest")
        self.assertContains(shared_page, prsb.name)
        self.assertNotContains(
            shared_page,
            reverse("band:gig_detail", kwargs={"slug": "guest", "pk": shared.pk}),
        )


class BandPageScopeTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prsb = Band.objects.get(slug="prsb")
        cls.other = Band.objects.create(name="Other", slug="other")
        cls.user = User.objects.create_user(username="scope_user", first_name="Scope", last_name="User")
        BandMembership.objects.create(band=cls.prsb, member=cls.user.bandmember)
        cls.song = Song.objects.create(title="Scoped Song")
        BandSong.objects.create(band=cls.prsb, song=cls.song, in_gig_rotation=True)

    def test_other_band_hides_prsb_roster_and_song(self):
        member_response = self.client.get(reverse("band:member_list", kwargs={"slug": "other"}))
        self.assertEqual(list(member_response.context["object_list"]), [])
        song_response = self.client.get(reverse("band:song_list", kwargs={"slug": "other"}))
        self.assertEqual(list(song_response.context["object_list"]), [])

    def test_deactivate_hides_member_and_link_reactivates(self):
        membership = BandMembership.objects.get(band=self.prsb, member=self.user.bandmember)
        membership.is_active = False
        membership.save()
        listed = self.client.get(reverse("band:member_list", kwargs={"slug": "prsb"}))
        self.assertEqual(list(listed.context["object_list"]), [])
        self.assertContains(listed, 'id="member-link-select"')
        self.assertContains(listed, "Scope User")
        self.client.post(
            reverse("band:member_link", kwargs={"slug": "prsb"}),
            {"member": self.user.bandmember.pk},
        )
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)

    def test_member_and_song_lists_have_no_deactivate_action(self):
        members = self.client.get(reverse("band:member_list", kwargs={"slug": "prsb"}))
        songs = self.client.get(reverse("band:song_list", kwargs={"slug": "prsb"}))
        self.assertNotContains(members, "Deactivate")
        self.assertNotContains(songs, "Deactivate")
        for url_name in ("band:member_unlink", "band:song_unlink"):
            with self.assertRaises(NoReverseMatch):
                reverse(url_name, kwargs={"slug": "prsb"})

    def test_song_detail_shows_parts_for_active_members_only(self):
        lead = SongPart.objects.create(song=self.song, name="Lead")
        pad = SongPart.objects.create(song=self.song, name="Pad")
        trumpet = Instrument.objects.create(name="Trumpet Scope", order=0)
        inactive_user = User.objects.create_user(
            username="inactive_part", first_name="Gone", last_name="Member",
        )
        BandMembership.objects.create(
            band=self.prsb, member=inactive_user.bandmember, is_active=False,
        )
        other_user = User.objects.create_user(
            username="other_part", first_name="Other", last_name="Band",
        )
        BandMembership.objects.create(band=self.other, member=other_user.bandmember)
        unassigned = User.objects.create_user(
            username="unassigned_part", first_name="Needs", last_name="Part",
        )
        BandMembership.objects.create(band=self.prsb, member=unassigned.bandmember)
        PartAssignment.objects.create(
            member=self.user.bandmember, song_part=lead, instrument=trumpet,
        )
        PartAssignment.objects.create(
            member=inactive_user.bandmember, song_part=lead, instrument=trumpet,
        )
        PartAssignment.objects.create(
            member=other_user.bandmember, song_part=lead, instrument=trumpet,
        )
        PartAssignment.objects.create(
            member=inactive_user.bandmember, song_part=pad, instrument=trumpet,
        )

        response = self.client.get(
            reverse("band:song_detail", kwargs={"slug": "prsb", "pk": self.song.pk}),
        )
        shown = list(response.context["part_assignments"])
        self.assertEqual([row.member for row in shown], [self.user.bandmember])
        self.assertEqual([part.name for part in response.context["missing_parts"]], ["Pad"])
        self.assertEqual(list(response.context["missing_members"]), [unassigned.bandmember])
        self.assertContains(response, "Scope User")
        self.assertNotContains(response, "Gone Member")
        self.assertNotContains(response, "Other Band")

    def test_song_active_on_other_band_appears_in_link_select(self):
        response = self.client.get(reverse("band:song_list", kwargs={"slug": "other"}))
        self.assertEqual(list(response.context["object_list"]), [])
        self.assertContains(response, 'id="song-link-select"')
        self.assertContains(response, "Scoped Song")

    def test_song_title_edit_is_global_and_rotation_is_local(self):
        BandSong.objects.create(band=self.other, song=self.song, in_gig_rotation=False)
        self.client.post(
            reverse("band:song_update", kwargs={"slug": "prsb", "pk": self.song.pk}),
            {"title": "Renamed", "composer": "", "duration": "", "form": "", "in_gig_rotation": "on",
             "parts-TOTAL_FORMS": "0", "parts-INITIAL_FORMS": "0", "parts-MIN_NUM_FORMS": "0",
             "parts-MAX_NUM_FORMS": "1000"},
        )
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, "Renamed")
        self.assertFalse(self.song.band_songs.get(band=self.other).in_gig_rotation)

    def test_posting_rotation_on_sets_this_band_and_leaves_the_other(self):
        self.song.band_songs.filter(band=self.prsb).update(in_gig_rotation=False)
        BandSong.objects.create(band=self.other, song=self.song, in_gig_rotation=False)
        response = self.client.post(
            reverse("band:song_update", kwargs={"slug": "prsb", "pk": self.song.pk}),
            {"title": self.song.title, "composer": "", "duration": "", "form": "", "in_gig_rotation": "on",
             "parts-TOTAL_FORMS": "0", "parts-INITIAL_FORMS": "0", "parts-MIN_NUM_FORMS": "0",
             "parts-MAX_NUM_FORMS": "1000"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.song.band_songs.get(band=self.prsb).in_gig_rotation)
        self.assertFalse(self.song.band_songs.get(band=self.other).in_gig_rotation)

    def test_song_create_posts_rotation_on_for_this_band(self):
        response = self.client.post(
            reverse("band:song_create", kwargs={"slug": "prsb"}),
            {"title": "Created With Rotation", "composer": "", "duration": "", "form": "", "in_gig_rotation": "on"},
        )
        self.assertEqual(response.status_code, 302)
        created = Song.objects.get(title="Created With Rotation")
        self.assertTrue(created.band_songs.get(band=self.prsb).in_gig_rotation)

    def test_inactive_band_post_to_create_endpoints_returns_403(self):
        inactive = Band.objects.create(name="Inactive", slug="inactive", is_active=False)
        cases = (
            ("band:song_create", {}),
            ("band:member_link", {"member": self.user.bandmember.pk}),
            ("band:song_link", {"song": self.song.pk}),
            ("band:gig_create", {}),
        )
        for url_name, data in cases:
            response = self.client.post(reverse(url_name, kwargs={"slug": inactive.slug}), data)
            self.assertEqual(response.status_code, 403, msg=url_name)


class PartAssignmentBandScopeTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prsb = Band.objects.get(slug="prsb")
        cls.other = Band.objects.create(name="Scope Other", slug="scope-other")
        cls.third = Band.objects.create(name="Scope Third", slug="scope-third")
        cls.player = User.objects.create_user(
            username="scope_player", first_name="Scope", last_name="Player",
        )
        cls.shared = User.objects.create_user(
            username="scope_shared", first_name="Shared", last_name="Player",
        )
        cls.outsider = User.objects.create_user(
            username="scope_outsider", first_name="Outside", last_name="Player",
        )
        cls.inactive_user = User.objects.create_user(
            username="scope_inactive_user", first_name="Inactive", last_name="Player", is_active=False,
        )
        BandMembership.objects.create(band=cls.prsb, member=cls.player.bandmember)
        BandMembership.objects.create(band=cls.other, member=cls.player.bandmember, is_active=False)
        BandMembership.objects.create(band=cls.prsb, member=cls.shared.bandmember)
        BandMembership.objects.create(band=cls.third, member=cls.shared.bandmember)
        BandMembership.objects.create(band=cls.other, member=cls.outsider.bandmember)
        BandMembership.objects.create(band=cls.prsb, member=cls.inactive_user.bandmember)
        cls.home_song = Song.objects.create(title="Home Song")
        cls.dropped_song = Song.objects.create(title="Dropped Song")
        cls.other_song = Song.objects.create(title="Other Band Song")
        cls.third_song = Song.objects.create(title="Third Band Song")
        cls.third_only_song = Song.objects.create(title="Third Only Song")
        cls.unlinked_song = Song.objects.create(title="Unlinked Song")
        BandSong.objects.create(band=cls.prsb, song=cls.home_song, is_active=True)
        BandSong.objects.create(band=cls.prsb, song=cls.dropped_song, is_active=False)
        BandSong.objects.create(band=cls.other, song=cls.other_song, is_active=True)
        BandSong.objects.create(band=cls.third, song=cls.third_song, is_active=True)
        BandSong.objects.create(band=cls.prsb, song=cls.third_song, is_active=True)
        BandSong.objects.create(band=cls.third, song=cls.third_only_song, is_active=True)
        cls.home_part = SongPart.objects.create(song=cls.home_song, name="Lead")
        cls.dropped_part = SongPart.objects.create(song=cls.dropped_song, name="Lead")
        cls.other_part = SongPart.objects.create(song=cls.other_song, name="Lead")
        cls.third_part = SongPart.objects.create(song=cls.third_song, name="Lead")
        cls.third_only_part = SongPart.objects.create(song=cls.third_only_song, name="Lead")
        cls.unlinked_part = SongPart.objects.create(song=cls.unlinked_song, name="Lead")
        cls.horn = Instrument.objects.create(name="Scope Horn", order=0)
        for part in (cls.home_part, cls.dropped_part, cls.other_part):
            PartAssignment.objects.create(
                member=cls.player.bandmember, song_part=part, instrument=cls.horn,
            )
        PartAssignment.objects.create(
            member=cls.shared.bandmember, song_part=cls.third_part, instrument=cls.horn,
        )
        PartAssignment.objects.create(
            member=cls.shared.bandmember, song_part=cls.third_only_part, instrument=cls.horn,
        )

    def test_member_detail_shows_songs_from_active_bands_only(self):
        response = self.client.get(
            reverse("band:member_detail", kwargs={"slug": "prsb", "pk": self.player.bandmember.pk}),
        )
        titles = [row.song_part.song.title for row in response.context["part_assignments"]]
        missing = [song.title for song in response.context["missing_songs"]]
        self.assertEqual(titles, ["Home Song"])
        self.assertIn("Third Band Song", missing)
        self.assertNotIn("Third Only Song", missing)
        self.assertNotIn("Dropped Song", missing)
        self.assertNotIn("Other Band Song", missing)
        self.assertNotIn("Unlinked Song", missing)
        self.assertNotContains(response, "Dropped Song")
        self.assertNotContains(response, "Other Band Song")

    def test_member_detail_includes_every_band_the_member_is_in(self):
        response = self.client.get(
            reverse("band:member_detail", kwargs={"slug": "prsb", "pk": self.shared.bandmember.pk}),
        )
        titles = [row.song_part.song.title for row in response.context["part_assignments"]]
        missing = {song.title for song in response.context["missing_songs"]}
        self.assertEqual(titles, ["Third Band Song", "Third Only Song"])
        self.assertIn("Home Song", missing)
        self.assertNotIn("Other Band Song", missing)

    def test_member_create_form_limits_song_parts_to_member_bands(self):
        response = self.client.get(reverse(
            "band:member_part_assignment_create",
            kwargs={"slug": "prsb", "member_id": self.player.bandmember.pk},
        ))
        part_ids = set(response.context["form"].fields["song_part"].queryset.values_list("pk", flat=True))
        self.assertIn(self.home_part.pk, part_ids)
        self.assertIn(self.third_part.pk, part_ids)
        self.assertNotIn(self.third_only_part.pk, part_ids)
        self.assertNotIn(self.other_part.pk, part_ids)
        self.assertNotIn(self.dropped_part.pk, part_ids)
        self.assertNotIn(self.unlinked_part.pk, part_ids)

        shared_form = self.client.get(reverse(
            "band:member_part_assignment_create",
            kwargs={"slug": "prsb", "member_id": self.shared.bandmember.pk},
        ))
        shared_part_ids = set(
            shared_form.context["form"].fields["song_part"].queryset.values_list("pk", flat=True)
        )
        self.assertIn(self.third_only_part.pk, shared_part_ids)
        self.assertNotIn(self.other_part.pk, shared_part_ids)

        rejected = self.client.post(reverse(
            "band:member_part_assignment_create",
            kwargs={"slug": "prsb", "member_id": self.player.bandmember.pk},
        ), {
            "member": self.player.bandmember.pk,
            "song_part": self.other_part.pk,
            "instrument": self.horn.pk,
            "performance_readiness": PerformanceReadiness.READY,
        })
        self.assertEqual(rejected.status_code, 200)
        self.assertIn("song_part", rejected.context["form"].errors)
        self.assertEqual(PartAssignment.objects.filter(
            member=self.player.bandmember, song_part=self.other_part,
        ).count(), 1)

    def test_song_create_form_limits_members_to_song_bands(self):
        response = self.client.get(reverse(
            "band:song_part_assignment_create",
            kwargs={"slug": "prsb", "song_id": self.third_song.pk},
        ))
        member_ids = set(response.context["form"].fields["member"].queryset.values_list("pk", flat=True))
        self.assertIn(self.player.bandmember.pk, member_ids)
        self.assertIn(self.shared.bandmember.pk, member_ids)
        self.assertNotIn(self.outsider.bandmember.pk, member_ids)
        self.assertNotIn(self.inactive_user.bandmember.pk, member_ids)

        only_third = self.client.get(reverse(
            "band:song_part_assignment_create",
            kwargs={"slug": "prsb", "song_id": self.third_only_song.pk},
        ))
        only_third_ids = set(only_third.context["form"].fields["member"].queryset.values_list("pk", flat=True))
        self.assertEqual(only_third_ids, {self.shared.bandmember.pk})

        rejected = self.client.post(reverse(
            "band:song_part_assignment_create",
            kwargs={"slug": "prsb", "song_id": self.third_song.pk},
        ), {
            "member": self.outsider.bandmember.pk,
            "song_part": self.third_part.pk,
            "instrument": self.horn.pk,
            "performance_readiness": PerformanceReadiness.READY,
        })
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(PartAssignment.objects.filter(
            member=self.outsider.bandmember, song_part=self.third_part,
        ).exists())


class GigStaffingValidationTestCase(TestCase):
    _band_counter = 0

    def make_two_band_gig(self):
        GigStaffingValidationTestCase._band_counter += 1
        n = GigStaffingValidationTestCase._band_counter
        first = Band.objects.create(name=f"Gig Staff A {n}", slug=f"gig-staff-a-{n}")
        second = Band.objects.create(name=f"Gig Staff B {n}", slug=f"gig-staff-b-{n}")
        gig = Gig.objects.create(
            name=f"Two-band gig {n}",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        gig.bands.set([first, second])
        return gig

    def link_song(self, band, title):
        song = Song.objects.create(title=title)
        BandSong.objects.create(band=band, song=song, is_active=True, in_gig_rotation=True)
        return song

    def test_multi_band_entries_can_name_different_bands(self):
        gig = self.make_two_band_gig()
        first, second = gig.bands.order_by("name")
        song_a = self.link_song(first, "Song A")
        song_b = self.link_song(second, "Song B")
        GigSetlistEntry.objects.create(gig=gig, song=song_a, staffing_mode="only", staffing_band=first, _order=0)
        GigSetlistEntry.objects.create(gig=gig, song=song_b, staffing_mode="only", staffing_band=second, _order=1)
        self.assertEqual(gig.gigsetlistentry_set.count(), 2)

    def test_removing_named_band_fails(self):
        gig = self.make_two_band_gig()
        first, second = list(gig.bands.all())
        song = self.link_song(first, "Kept")
        entry = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=first,
        )
        with self.assertRaises(ValidationError):
            validate_gig_bands(gig, [second], [entry])

    def test_deactivating_song_link_leaves_old_entry_valid(self):
        gig = self.make_two_band_gig()
        first = gig.bands.order_by("name").first()
        song = self.link_song(first, "Old")
        entry = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=first,
        )
        BandSong.objects.filter(band=first, song=song).update(is_active=False)
        validate_entry_change(entry, previous=entry, gig_bands=set(gig.bands.all()))

    def test_new_only_entry_requires_active_repertoire(self):
        gig = self.make_two_band_gig()
        first, second = list(gig.bands.order_by("name"))
        song = Song.objects.create(title="Unlinked")
        entry = GigSetlistEntry(gig=gig, song=song, staffing_mode="only", staffing_band=first)
        with self.assertRaises(ValidationError):
            validate_entry_change(entry, previous=None, gig_bands=set(gig.bands.all()))

    def _gig_update_post_data(self, gig, band_ids):
        return {
            "name": gig.name,
            "start_datetime": gig.start_datetime.strftime("%Y-%m-%dT%H:%M"),
            "end_datetime": gig.end_datetime.strftime("%Y-%m-%dT%H:%M"),
            "address": "",
            "notes": "",
            "include_in_stats": "",
            "bands": [str(pk) for pk in band_ids],
            "giginstrument_set-TOTAL_FORMS": "0",
            "giginstrument_set-INITIAL_FORMS": "0",
            "giginstrument_set-MIN_NUM_FORMS": "0",
            "giginstrument_set-MAX_NUM_FORMS": "1000",
        }

    def test_one_band_gig_update_clears_staffing_fields(self):
        gig = self.make_two_band_gig()
        first, second = list(gig.bands.order_by("name"))
        song = self.link_song(first, "Staffed")
        entry = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=first,
        )
        url = reverse("band:gig_update", kwargs={"slug": first.slug, "pk": gig.pk})
        response = self.client.post(url, self._gig_update_post_data(gig, [first.pk]))
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.staffing_mode, "")
        self.assertIsNone(entry.staffing_band_id)

    def test_override_rejects_member_outside_entry_pool(self):
        gig = self.make_two_band_gig()
        first, second = list(gig.bands.order_by("name"))
        song = self.link_song(first, "Pool Song")
        GigSetlistEntry.objects.create(gig=gig, song=song, staffing_mode="only", staffing_band=first)
        part = SongPart.objects.create(song=song, name="Part")
        drums = Instrument.objects.create(name=f"Pool drums {gig.pk}", order=0)
        gig_drums = GigInstrument.objects.create(gig=gig, instrument=drums, gig_quantity=1)

        in_pool = User.objects.create_user(username=f"pool_in_{gig.pk}", first_name="In", last_name="Pool")
        out_pool = User.objects.create_user(username=f"pool_out_{gig.pk}", first_name="Out", last_name="Pool")
        BandMembership.objects.create(band=first, member=in_pool.bandmember, is_active=True)
        BandMembership.objects.create(band=second, member=out_pool.bandmember, is_active=True)
        for member in (in_pool.bandmember, out_pool.bandmember):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)

        form = GigPartAssignmentOverrideForm(
            data={
                "override_type": OverrideType.NOT_PLAYING,
                "member": out_pool.bandmember.pk,
                "song_part": part.pk,
                "gig_instrument": gig_drums.pk,
            },
            gig_id=gig.pk,
            gig_name=gig.name,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("member", form.errors)

    def test_override_rejects_member_outside_any_entry_pool_for_the_song(self):
        gig = self.make_two_band_gig()
        first, second = list(gig.bands.order_by("name"))
        song = self.link_song(first, "Two Pool Song")
        BandSong.objects.create(band=second, song=song, is_active=True, in_gig_rotation=True)
        GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=first, _order=0,
        )
        GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=second, _order=1,
        )
        part = SongPart.objects.create(song=song, name="Part")
        drums = Instrument.objects.create(name=f"Two-pool drums {gig.pk}", order=0)
        gig_drums = GigInstrument.objects.create(gig=gig, instrument=drums, gig_quantity=1)
        in_first = User.objects.create_user(
            username=f"two_pool_{gig.pk}", first_name="First", last_name="Only",
        )
        BandMembership.objects.create(band=first, member=in_first.bandmember, is_active=True)
        GigAttendance.objects.create(gig=gig, member=in_first.bandmember, status=GigAttendance.AVAILABLE)

        form = GigPartAssignmentOverrideForm(
            data={
                "override_type": OverrideType.NOT_PLAYING,
                "member": in_first.bandmember.pk,
                "song_part": part.pk,
                "gig_instrument": gig_drums.pk,
            },
            gig_id=gig.pk,
            gig_name=gig.name,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("member", form.errors)

    def test_setlist_save_keeps_entry_whose_song_link_is_inactive(self):
        gig = self.make_two_band_gig()
        first = gig.bands.order_by("name").first()
        song = self.link_song(first, "Stale Link Song")
        entry = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=first,
        )
        BandSong.objects.filter(band=first, song=song).update(is_active=False)
        url = reverse("band:gig_setlist_update", kwargs={"slug": first.slug, "gig_id": gig.pk})
        response = self.client.post(url, {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(entry.pk),
            "form-0-song": str(song.pk),
            "form-0-break_duration": "",
            "form-0-staffing_mode": "only",
            "form-0-staffing_band": str(first.pk),
        })
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.song_id, song.pk)

    def test_add_song_on_one_band_gig_creates_entry(self):
        prsb = Band.objects.get(slug=PRSB_SLUG)
        gig = Gig.objects.create(
            name="One-band add song",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        gig.bands.set([prsb])
        song = self.link_song(prsb, "Quick add")
        url = reverse(
            "band:gig_setlist_add_song",
            kwargs={"slug": prsb.slug, "gig_id": gig.pk, "song_id": song.pk},
        )
        response = self.client.post(url)
        self.assertRedirects(
            response,
            reverse("band:gig_part_assignments_detail", kwargs={"slug": prsb.slug, "pk": gig.pk}),
        )
        entry = gig.gigsetlistentry_set.get()
        self.assertEqual(entry.song_id, song.pk)
        self.assertEqual(entry.staffing_mode, "")
        self.assertIsNone(entry.staffing_band_id)

    def test_add_song_on_multi_band_gig_redirects_without_creating_entry(self):
        gig = self.make_two_band_gig()
        first = gig.bands.order_by("name").first()
        song = self.link_song(first, "Needs staffing")
        url = reverse(
            "band:gig_setlist_add_song",
            kwargs={"slug": first.slug, "gig_id": gig.pk, "song_id": song.pk},
        )
        response = self.client.post(url)
        self.assertRedirects(
            response,
            reverse("band:gig_setlist_update", kwargs={"slug": first.slug, "gig_id": gig.pk}),
        )
        self.assertEqual(gig.gigsetlistentry_set.count(), 0)


class BandSongRotationTestCase(TestCase):
    def test_rotation_is_per_band(self):
        song = Song.objects.create(title="Shared Tune")
        first = Band.objects.create(name="First", slug="first")
        second = Band.objects.create(name="Second", slug="second")
        BandSong.objects.create(band=first, song=song, in_gig_rotation=True)
        BandSong.objects.create(band=second, song=song, in_gig_rotation=False)
        self.assertTrue(song.band_songs.get(band=first).in_gig_rotation)
        self.assertFalse(song.band_songs.get(band=second).in_gig_rotation)
        self.assertNotIn("in_gig_rotation", {field.name for field in Song._meta.fields})


class MultiBandSolverTestCase(TestCase):
    _n = 0

    def _next_id(self):
        MultiBandSolverTestCase._n += 1
        return MultiBandSolverTestCase._n

    def _gig(self, n, bands):
        gig = Gig.objects.create(
            name=f"Solver gig {n}",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
        )
        gig.bands.set(bands)
        return gig

    def scenario(self, mode="only", preferred_knows_part=True):
        n = self._next_id()
        preferred_band = Band.objects.create(name=f"Solver Preferred {n}", slug=f"solver-preferred-{n}")
        other_band = Band.objects.create(name=f"Solver Other {n}", slug=f"solver-other-{n}")
        gig = self._gig(n, [preferred_band, other_band])
        instrument = Instrument.objects.create(name=f"Solver Horn {n}", quantity=1, order=0)
        GigInstrument.objects.create(gig=gig, instrument=instrument, gig_quantity=1)

        song = Song.objects.create(title=f"Solver Song {n}")
        for band in (preferred_band, other_band):
            BandSong.objects.create(band=band, song=song, is_active=True, in_gig_rotation=True)
        part = SongPart.objects.create(song=song, name="Lead")

        preferred_user = User.objects.create_user(
            username=f"solver_pref_{n}", first_name="Pref", last_name=f"P{n}",
        )
        other_user = User.objects.create_user(
            username=f"solver_other_{n}", first_name="Other", last_name=f"O{n}",
        )
        preferred = preferred_user.bandmember
        other = other_user.bandmember
        BandMembership.objects.create(band=preferred_band, member=preferred)
        BandMembership.objects.create(band=other_band, member=other)
        for member in (preferred, other):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
        if preferred_knows_part:
            PartAssignment.objects.create(
                member=preferred, song_part=part, instrument=instrument,
                performance_readiness=PerformanceReadiness.READY,
            )
        PartAssignment.objects.create(
            member=other, song_part=part, instrument=instrument,
            performance_readiness=PerformanceReadiness.READY,
        )
        entry = GigSetlistEntry.objects.create(
            gig=gig,
            song=song,
            staffing_mode=mode,
            staffing_band=None if mode == "any" else preferred_band,
        )
        return gig, entry, preferred, other

    def test_only_never_assigns_the_other_band(self):
        gig, entry, preferred, other = self.scenario(mode="only")
        setlist, _, _ = get_gig_part_assignments(gig, [])
        members = {row.member for row in setlist[0].part_assignments}
        self.assertIn(preferred, members)
        self.assertNotIn(other, members)

    def test_prefer_fills_a_hole_with_the_other_band(self):
        gig, entry, preferred, other = self.scenario(mode="prefer", preferred_knows_part=False)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, other)

    def test_prefer_keeps_preferred_player_when_both_cover(self):
        gig, entry, preferred, other = self.scenario(mode="prefer", preferred_knows_part=True)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, preferred)

    def test_dual_member_counts_as_preferred(self):
        gig, entry, preferred, other = self.scenario(mode="prefer", preferred_knows_part=True)
        BandMembership.objects.create(band=entry.staffing_band, member=other)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, preferred)

    def test_any_may_use_either_roster(self):
        gig, entry, preferred, other = self.scenario(mode="any", preferred_knows_part=False)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, other)

    def test_only_entries_are_solved_before_prefer(self):
        # Equal candidate counts and a Prefer title that sorts first. A title
        # sort solves Prefer first and keeps the shared player; Only-first
        # order spends that player and leaves Prefer the alternate.
        n = self._next_id()
        band_a = Band.objects.create(name=f"Order A {n}", slug=f"order-a-{n}")
        band_b = Band.objects.create(name=f"Order B {n}", slug=f"order-b-{n}")
        gig = self._gig(n, [band_a, band_b])
        instrument = Instrument.objects.create(name=f"Order Horn {n}", quantity=1, order=0)
        GigInstrument.objects.create(gig=gig, instrument=instrument, gig_quantity=1)

        shared_user = User.objects.create_user(username=f"order_shared_{n}", first_name="Shared", last_name=f"S{n}")
        extra_user = User.objects.create_user(username=f"order_extra_{n}", first_name="Extra", last_name=f"E{n}")
        alternate_user = User.objects.create_user(
            username=f"order_alt_{n}", first_name="Alternate", last_name=f"N{n}",
        )
        shared = shared_user.bandmember
        extra = extra_user.bandmember
        alternate = alternate_user.bandmember
        for member in (shared, extra, alternate):
            BandMembership.objects.create(band=band_a, member=member)
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)

        def song_with_parts(title, ready_members, backup_members):
            song = Song.objects.create(title=title)
            BandSong.objects.create(band=band_a, song=song, is_active=True, in_gig_rotation=True)
            BandSong.objects.create(band=band_b, song=song, is_active=True, in_gig_rotation=True)
            part = SongPart.objects.create(song=song, name="Lead")
            for member in ready_members:
                PartAssignment.objects.create(
                    member=member, song_part=part, instrument=instrument,
                    performance_readiness=PerformanceReadiness.READY,
                )
            for member in backup_members:
                PartAssignment.objects.create(
                    member=member, song_part=part, instrument=instrument,
                    performance_readiness=PerformanceReadiness.BACKUP,
                )
            return song

        wide = song_with_parts("M Only Wide", [shared], [extra])
        prefer = song_with_parts("A Prefer Song", [shared, alternate], [])
        scarce = song_with_parts("Z Only Scarce", [shared], [extra])
        GigSetlistEntry.objects.create(gig=gig, song=wide, staffing_mode="only", staffing_band=band_a)
        GigSetlistEntry.objects.create(
            gig=gig, song=prefer, staffing_mode="prefer", staffing_band=band_b,
        )
        GigSetlistEntry.objects.create(gig=gig, song=scarce, staffing_mode="only", staffing_band=band_a)

        setlist, _, _ = get_gig_part_assignments(gig, [])
        by_title = {row.song.title: row.part_assignments[0].member for row in setlist}
        self.assertEqual(by_title["M Only Wide"], shared)
        self.assertEqual(by_title["Z Only Scarce"], shared)
        self.assertEqual(by_title["A Prefer Song"], alternate)

    def test_same_song_on_two_entries_assigns_each_band(self):
        n = self._next_id()
        band_a = Band.objects.create(name=f"Split A {n}", slug=f"split-a-{n}")
        band_b = Band.objects.create(name=f"Split B {n}", slug=f"split-b-{n}")
        gig = self._gig(n, [band_a, band_b])
        instrument = Instrument.objects.create(name=f"Split Horn {n}", quantity=1, order=0)
        GigInstrument.objects.create(gig=gig, instrument=instrument, gig_quantity=1)
        song = Song.objects.create(title=f"Split Song {n}")
        for band in (band_a, band_b):
            BandSong.objects.create(band=band, song=song, is_active=True, in_gig_rotation=True)
        part = SongPart.objects.create(song=song, name="Lead")

        user_a = User.objects.create_user(username=f"split_a_{n}", first_name="Ada", last_name=f"A{n}")
        user_b = User.objects.create_user(username=f"split_b_{n}", first_name="Bea", last_name=f"B{n}")
        member_a = user_a.bandmember
        member_b = user_b.bandmember
        BandMembership.objects.create(band=band_a, member=member_a)
        BandMembership.objects.create(band=band_b, member=member_b)
        for member in (member_a, member_b):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
            PartAssignment.objects.create(
                member=member, song_part=part, instrument=instrument,
                performance_readiness=PerformanceReadiness.READY,
            )
        entry_a = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=band_a,
        )
        entry_b = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="only", staffing_band=band_b,
        )

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(
            [(row.setlist_entry, row.part_assignments[0].member) for row in setlist],
            [(entry_a, member_a), (entry_b, member_b)],
        )

    def test_inactive_membership_still_assigned_for_only(self):
        gig, entry, preferred, other = self.scenario(mode="only")
        BandMembership.objects.filter(band=entry.staffing_band, member=preferred).update(is_active=False)
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].part_assignments[0].member, preferred)

    def test_out_of_rotation_setlist_song_and_band_recommendations(self):
        n = self._next_id()
        band_a = Band.objects.create(name=f"Rec A {n}", slug=f"rec-a-{n}")
        band_b = Band.objects.create(name=f"Rec B {n}", slug=f"rec-b-{n}")
        outsider = Band.objects.create(name=f"Rec Out {n}", slug=f"rec-out-{n}")
        gig = self._gig(n, [band_a, band_b])
        instrument = Instrument.objects.create(name=f"Rec Horn {n}", quantity=1, order=0)
        GigInstrument.objects.create(gig=gig, instrument=instrument, gig_quantity=1)

        user_a = User.objects.create_user(username=f"rec_a_{n}", first_name="Rec", last_name=f"A{n}")
        user_b = User.objects.create_user(username=f"rec_b_{n}", first_name="Rec", last_name=f"B{n}")
        player_a = user_a.bandmember
        player_b = user_b.bandmember
        BandMembership.objects.create(band=band_a, member=player_a)
        BandMembership.objects.create(band=band_b, member=player_b)
        for member in (player_a, player_b):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)

        def make_song(title, band, player, in_rotation, active=True):
            song = Song.objects.create(title=title)
            BandSong.objects.create(
                band=band, song=song, is_active=active, in_gig_rotation=in_rotation,
            )
            part = SongPart.objects.create(song=song, name="Lead")
            PartAssignment.objects.create(
                member=player, song_part=part, instrument=instrument,
                performance_readiness=PerformanceReadiness.READY,
            )
            return song

        out_of_rotation = make_song("Setlist Out", band_a, player_a, in_rotation=False)
        on_setlist = make_song("Setlist In", band_a, player_a, in_rotation=True)
        rec_a = make_song("Rec Band A", band_a, player_a, in_rotation=True)
        rec_b = make_song("Rec Band B", band_b, player_b, in_rotation=True)
        make_song("Inactive Rotation", band_a, player_a, in_rotation=True, active=False)
        make_song("Outsider Rotation", outsider, player_a, in_rotation=True)
        GigSetlistEntry.objects.create(
            gig=gig, song=out_of_rotation, staffing_mode="only", staffing_band=band_a,
        )
        GigSetlistEntry.objects.create(
            gig=gig, song=on_setlist, staffing_mode="only", staffing_band=band_a,
        )

        setlist, recs, _ = get_gig_part_assignments(gig, [])
        by_title = {row.song.title: row for row in setlist}
        self.assertEqual(set(by_title), {"Setlist Out", "Setlist In"})
        self.assertEqual(by_title["Setlist Out"].part_assignments[0].member, player_a)
        self.assertEqual({row.song for row in recs}, {rec_a, rec_b})

    def _drum_cover_gig(self, mode):
        n = self._next_id()
        band_a = Band.objects.create(name=f"Kit A {n}", slug=f"kit-a-{n}")
        band_b = Band.objects.create(name=f"Kit B {n}", slug=f"kit-b-{n}")
        gig = self._gig(n, [band_a, band_b])
        kit = Instrument.objects.create(
            name=f"Kit Drum {n}", quantity=1, order=0, is_drum_kit_cover_instrument=True,
        )
        GigInstrument.objects.create(gig=gig, instrument=kit, gig_quantity=1)
        song = Song.objects.create(title=f"Kit Song {n}")
        BandSong.objects.create(band=band_a, song=song, is_active=True, in_gig_rotation=True)
        engine = SongPart.objects.create(song=song, name="Engine")

        listed_user = User.objects.create_user(username=f"kit_listed_{n}", first_name="Listed", last_name=f"L{n}")
        cover_user = User.objects.create_user(username=f"kit_cover_{n}", first_name="Cover", last_name=f"C{n}")
        listed = listed_user.bandmember
        cover = cover_user.bandmember
        BandMembership.objects.create(band=band_a, member=listed)
        BandMembership.objects.create(band=band_b, member=cover)
        GigAttendance.objects.create(gig=gig, member=listed, status=GigAttendance.UNAVAILABLE)
        GigAttendance.objects.create(gig=gig, member=cover, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=listed, song_part=engine, instrument=kit,
            performance_readiness=PerformanceReadiness.READY,
        )
        DrumKitCoverPlayer.objects.create(band=band_b, member=cover, priority=1)
        entry = GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode=mode, staffing_band=band_a,
        )
        return gig, entry, cover, engine

    def test_only_ignores_the_other_bands_drum_cover(self):
        gig, entry, cover, engine = self._drum_cover_gig("only")
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        self.assertNotIn(cover, {row.member for row in setlist[0].part_assignments})
        self.assertEqual(setlist[0].unplayed_parts, [engine])

    def test_prefer_uses_another_bands_drum_cover(self):
        gig, entry, cover, engine = self._drum_cover_gig("prefer")
        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(len(setlist), 1)
        self.assertEqual(setlist[0].part_assignments[0].member, cover)
        self.assertTrue(setlist[0].part_assignments[0].is_drum_kit_cover)

    def test_prefer_fills_open_seat_and_keeps_preferred_players(self):
        n = self._next_id()
        preferred_band = Band.objects.create(name=f"Solo Pref {n}", slug=f"solo-pref-{n}")
        other_band = Band.objects.create(name=f"Solo Other {n}", slug=f"solo-other-{n}")
        gig = self._gig(n, [preferred_band, other_band])
        horn = Instrument.objects.create(name=f"Solo Horn {n}", quantity=1, order=0)
        plentiful = Instrument.objects.create(name=f"Solo Plenty {n}", quantity=10, order=1)
        GigInstrument.objects.create(gig=gig, instrument=horn, gig_quantity=1)
        GigInstrument.objects.create(gig=gig, instrument=plentiful, gig_quantity=10)

        song = Song.objects.create(title=f"Stacked Solo {n}")
        for band in (preferred_band, other_band):
            BandSong.objects.create(band=band, song=song, is_active=True, in_gig_rotation=True)
        lead = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        pad = SongPart.objects.create(song=song, name="Pad", has_solo=True)

        pat = User.objects.create_user(
            username=f"stack_pat_{n}", first_name="Pat", last_name=f"P{n}",
        ).bandmember
        sam = User.objects.create_user(
            username=f"stack_sam_{n}", first_name="Sam", last_name=f"S{n}",
        ).bandmember
        nina = User.objects.create_user(
            username=f"stack_nina_{n}", first_name="Nina", last_name=f"N{n}",
        ).bandmember
        BandMembership.objects.create(band=preferred_band, member=pat)
        BandMembership.objects.create(band=preferred_band, member=sam)
        BandMembership.objects.create(band=other_band, member=nina)
        for member in (pat, sam, nina):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
        PartAssignment.objects.create(
            member=pat, song_part=lead, instrument=horn,
            performance_readiness=PerformanceReadiness.READY, can_solo=True,
        )
        PartAssignment.objects.create(
            member=pat, song_part=pad, instrument=plentiful,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        PartAssignment.objects.create(
            member=sam, song_part=lead, instrument=plentiful,
            performance_readiness=PerformanceReadiness.READY, can_solo=False,
        )
        PartAssignment.objects.create(
            member=nina, song_part=pad, instrument=plentiful,
            performance_readiness=PerformanceReadiness.READY, can_solo=True,
        )
        GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="prefer", staffing_band=preferred_band,
        )

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(setlist[0].unplayed_parts, [])
        by_member = {row.member: row for row in setlist[0].part_assignments}
        self.assertEqual(set(by_member), {pat, sam, nina})
        self.assertEqual(by_member[pat].instrument, horn)
        self.assertEqual(by_member[pat].song_part, lead)
        self.assertEqual(by_member[nina].song_part, pad)

    def test_prefer_fills_open_lead_seats_from_the_other_band(self):
        n = self._next_id()
        preferred_band = Band.objects.create(name=f"Open Lead Pref {n}", slug=f"open-lead-pref-{n}")
        other_band = Band.objects.create(name=f"Open Lead Other {n}", slug=f"open-lead-other-{n}")
        gig = self._gig(n, [preferred_band, other_band])
        lead_pan = Instrument.objects.create(name=f"Open Lead Pan {n}", quantity=5, order=0)
        GigInstrument.objects.create(gig=gig, instrument=lead_pan, gig_quantity=2)
        song = Song.objects.create(title=f"Open Lead Song {n}")
        for band in (preferred_band, other_band):
            BandSong.objects.create(band=band, song=song, is_active=True, in_gig_rotation=True)
        part = SongPart.objects.create(song=song, name="Lead")
        preferred = User.objects.create_user(
            username=f"open_lead_pref_{n}", first_name="Pref", last_name=f"L{n}",
        ).bandmember
        other = User.objects.create_user(
            username=f"open_lead_other_{n}", first_name="Other", last_name=f"L{n}",
        ).bandmember
        BandMembership.objects.create(band=preferred_band, member=preferred)
        BandMembership.objects.create(band=other_band, member=other)
        for member in (preferred, other):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
            PartAssignment.objects.create(
                member=member, song_part=part, instrument=lead_pan,
                performance_readiness=PerformanceReadiness.READY,
            )
        GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="prefer", staffing_band=preferred_band,
        )

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(
            {row.member for row in setlist[0].part_assignments},
            {preferred, other},
        )

    def test_prefer_keeps_preferred_seat_against_nonpreferred_soloist(self):
        n = self._next_id()
        preferred_band = Band.objects.create(name=f"Seat Pref {n}", slug=f"seat-pref-{n}")
        other_band = Band.objects.create(name=f"Seat Other {n}", slug=f"seat-other-{n}")
        gig = self._gig(n, [preferred_band, other_band])
        horn = Instrument.objects.create(name=f"Seat Horn {n}", quantity=1, order=0)
        GigInstrument.objects.create(gig=gig, instrument=horn, gig_quantity=1)
        song = Song.objects.create(title=f"Seat Song {n}")
        for band in (preferred_band, other_band):
            BandSong.objects.create(band=band, song=song, is_active=True, in_gig_rotation=True)
        part = SongPart.objects.create(song=song, name="Lead", has_solo=True)
        preferred = User.objects.create_user(
            username=f"seat_pref_{n}", first_name="Pref", last_name=f"S{n}",
        ).bandmember
        other = User.objects.create_user(
            username=f"seat_other_{n}", first_name="Other", last_name=f"S{n}",
        ).bandmember
        BandMembership.objects.create(band=preferred_band, member=preferred)
        BandMembership.objects.create(band=other_band, member=other)
        for member, can_solo in ((preferred, False), (other, True)):
            GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
            PartAssignment.objects.create(
                member=member, song_part=part, instrument=horn,
                performance_readiness=PerformanceReadiness.READY, can_solo=can_solo,
            )
        GigSetlistEntry.objects.create(
            gig=gig, song=song, staffing_mode="prefer", staffing_band=preferred_band,
        )

        setlist, _, _ = get_gig_part_assignments(gig, [])
        self.assertEqual(
            [row.member for row in setlist[0].part_assignments],
            [preferred],
        )

    def test_print_and_by_member_keep_each_setlist_entry_number(self):
        n = self._next_id()
        band = Band.objects.create(name=f"Number Band {n}", slug=f"number-band-{n}")
        gig = self._gig(n, [band])
        instrument = Instrument.objects.create(name=f"Number Inst {n}", quantity=1, order=0)
        GigInstrument.objects.create(gig=gig, instrument=instrument, gig_quantity=1)
        song_a = Song.objects.create(title=f"Repeat Song {n}")
        song_b = Song.objects.create(title=f"Middle Song {n}")
        member = User.objects.create_user(
            username=f"number_player_{n}", first_name="Num", last_name=f"P{n}",
        ).bandmember
        BandMembership.objects.create(band=band, member=member)
        GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
        for song in (song_a, song_b):
            BandSong.objects.create(band=band, song=song, is_active=True, in_gig_rotation=True)
            part = SongPart.objects.create(song=song, name="Lead")
            PartAssignment.objects.create(
                member=member, song_part=part, instrument=instrument,
                performance_readiness=PerformanceReadiness.READY,
            )
        GigSetlistEntry.objects.create(gig=gig, song=song_a, _order=0)
        GigSetlistEntry.objects.create(gig=gig, song=song_b, _order=1)
        GigSetlistEntry.objects.create(gig=gig, song=song_a, _order=2)

        printed = self.client.get(
            reverse("band:gig_part_assignment_print", kwargs={"slug": band.slug, "pk": gig.pk}),
            {"order": "setlist"},
        )
        headings = re.findall(r"<h2>(.*?)</h2>", printed.content.decode())
        self.assertEqual(headings, [song_a.title, song_b.title, song_a.title])

        by_member = self.client.get(
            reverse("band:gig_part_assignment_by_member", kwargs={"slug": band.slug, "pk": gig.pk}),
        )
        rows = by_member.context["member_assignments"][0][1]
        entries = list(GigSetlistEntry.objects.filter(gig=gig).order_by("_order"))
        self.assertEqual([row[0] for row in rows], [entry._order + 1 for entry in entries])


class BulkEditPartAssignmentTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.band = Band.objects.get(slug="prsb")
        cls.other_band = Band.objects.create(name="Other Bulk Band", slug="other-bulk-band")
        cls.song = Song.objects.create(title="Bulk Edit Song")
        BandSong.objects.create(band=cls.band, song=cls.song, in_gig_rotation=True)
        cls.lead = SongPart.objects.create(song=cls.song, name="Bulk Lead", has_solo=True)
        cls.pad = SongPart.objects.create(song=cls.song, name="Bulk Pad", has_solo=False)
        cls.other_song = Song.objects.create(title="Bulk Other Song")
        cls.foreign_part = SongPart.objects.create(song=cls.other_song, name="Foreign Part")
        cls.trumpet = Instrument.objects.create(name="Bulk Trumpet", order=10)
        cls.trombone = Instrument.objects.create(name="Bulk Trombone", order=11)
        cls.ada = User.objects.create_user(username="bulk_ada", first_name="Ada", last_name="Alpha")
        cls.bea = User.objects.create_user(username="bulk_bea", first_name="Bea", last_name="Beta")
        cls.cy = User.objects.create_user(username="bulk_cy", first_name="Cy", last_name="Inactive")
        cls.dee = User.objects.create_user(username="bulk_dee", first_name="Dee", last_name="Other")
        BandMembership.objects.create(band=cls.band, member=cls.ada.bandmember, is_active=True)
        BandMembership.objects.create(band=cls.band, member=cls.bea.bandmember, is_active=True)
        BandMembership.objects.create(band=cls.band, member=cls.cy.bandmember, is_active=False)
        BandMembership.objects.create(band=cls.other_band, member=cls.dee.bandmember, is_active=True)

    def _assign(self, user, part, instrument, readiness=PerformanceReadiness.READY, can_solo=False):
        return PartAssignment.objects.create(
            member=user.bandmember,
            song_part=part,
            instrument=instrument,
            performance_readiness=readiness,
            can_solo=can_solo,
        )

    def _post(self, **fields):
        data = QueryDict(mutable=True)
        for key, value in fields.items():
            if isinstance(value, list):
                data.setlist(key, [str(item) for item in value])
            else:
                data[key] = "" if value is None else str(value)
        return data

    def _apply(self, data):
        from band.part_assignment_bulk import apply_bulk_part_assignments, parse_posted_rows
        return apply_bulk_part_assignments(self.song, self.band, parse_posted_rows(data))

    def _snapshot(self):
        return set(
            PartAssignment.objects.filter(song_part__song=self.song).values_list(
                "member_id", "song_part_id", "instrument_id", "performance_readiness", "can_solo",
            )
        )

    def test_new_chip_creates_ready_no_solo(self):
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        assignment = PartAssignment.objects.get(
            member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet,
        )
        self.assertEqual(assignment.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(assignment.can_solo)

    def test_member_added_to_existing_row_is_ready_and_not_solo(self):
        self._assign(self.ada, self.lead, self.trumpet, PerformanceReadiness.BACKUP, can_solo=True)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [self.ada.bandmember.pk, self.bea.bandmember.pk],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        ada = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(ada.performance_readiness, PerformanceReadiness.BACKUP)
        self.assertTrue(ada.can_solo)
        bea = PartAssignment.objects.get(member=self.bea.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(bea.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(bea.can_solo)

    def test_kept_chip_preserves_readiness_and_solo(self):
        self._assign(self.ada, self.lead, self.trumpet, PerformanceReadiness.BACKUP, can_solo=True)
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [self.ada.bandmember.pk],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertEqual(self._snapshot(), before)

    def test_removed_chip_deletes_assignment(self):
        self._assign(self.ada, self.lead, self.trumpet, PerformanceReadiness.BACKUP, can_solo=True)
        self._assign(self.bea, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [self.ada.bandmember.pk],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertFalse(PartAssignment.objects.filter(member=self.bea.bandmember, song_part=self.lead).exists())
        kept = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead)
        self.assertEqual(kept.performance_readiness, PerformanceReadiness.BACKUP)
        self.assertTrue(kept.can_solo)

    def test_clearing_chips_deletes_this_bands_assignments(self):
        self._assign(self.ada, self.lead, self.trumpet)
        self._assign(self.bea, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertFalse(
            PartAssignment.objects.filter(song_part=self.lead, instrument=self.trumpet, member__in=[self.ada.bandmember, self.bea.bandmember]).exists()
        )

    def test_other_band_member_is_not_deleted(self):
        self._assign(self.ada, self.lead, self.trumpet)
        self._assign(self.dee, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertFalse(PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead).exists())
        self.assertTrue(PartAssignment.objects.filter(member=self.dee.bandmember, song_part=self.lead, instrument=self.trumpet).exists())

    def test_inactive_member_assignment_is_not_deleted(self):
        self._assign(self.ada, self.lead, self.trumpet)
        self._assign(self.cy, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [],
            "new-TOTAL": 0,
        }))
        self.assertEqual(errors, [])
        self.assertTrue(PartAssignment.objects.filter(member=self.cy.bandmember, song_part=self.lead, instrument=self.trumpet).exists())
        self.assertFalse(PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead).exists())

    def test_duplicate_instrument_on_a_part_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 2,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
            "new-1-part": self.lead.pk,
            "new-1-instrument": self.trumpet.pk,
            "new-1-members": [self.bea.bandmember.pk],
        }))
        self.assertIn("Each instrument can only appear once on a part.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_new_row_cannot_reuse_an_existing_instrument(self):
        self._assign(self.ada, self.lead, self.trumpet)
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trumpet.pk,
            "existing-0-members": [],
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.bea.bandmember.pk],
        }))
        self.assertIn("Each instrument can only appear once on a part.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_new_row_with_members_and_no_instrument_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": "",
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertIn("Choose an instrument for each new row that has members.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_member_not_in_band_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.dee.bandmember.pk],
        }))
        self.assertIn("A selected member is not in this band.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_part_from_another_song_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.foreign_part.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertIn("A part on this form is not on this song.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_unknown_instrument_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": 999999,
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertIn("Choose a real instrument.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_unknown_existing_row_rejects_save(self):
        before = self._snapshot()
        errors = self._apply(self._post(**{
            "existing-TOTAL": 1,
            "existing-0-part": self.lead.pk,
            "existing-0-instrument": self.trombone.pk,
            "existing-0-members": [self.ada.bandmember.pk],
            "new-TOTAL": 0,
        }))
        self.assertIn("That part and instrument is not an existing row for this band.", errors)
        self.assertEqual(self._snapshot(), before)

    def test_duplicate_member_on_one_row_creates_one_assignment(self):
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk, self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        self.assertEqual(
            PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet).count(),
            1,
        )

    def test_empty_new_row_is_ignored(self):
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 2,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [],
            "new-1-part": self.lead.pk,
            "new-1-instrument": self.trombone.pk,
            "new-1-members": [self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        self.assertFalse(PartAssignment.objects.filter(song_part=self.lead, instrument=self.trumpet).exists())
        assignment = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead, instrument=self.trombone)
        self.assertEqual(assignment.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(assignment.can_solo)

    def test_new_row_skips_member_already_assigned(self):
        self._assign(self.ada, self.lead, self.trumpet, PerformanceReadiness.BACKUP, can_solo=True)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk, self.bea.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        ada = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(ada.performance_readiness, PerformanceReadiness.BACKUP)
        self.assertTrue(ada.can_solo)
        bea = PartAssignment.objects.get(member=self.bea.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(bea.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(bea.can_solo)

    def test_same_member_can_be_on_two_instruments(self):
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 2,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
            "new-1-part": self.lead.pk,
            "new-1-instrument": self.trombone.pk,
            "new-1-members": [self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        self.assertEqual(PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead).count(), 2)

    def test_new_row_can_use_instrument_held_only_outside_this_band(self):
        self._assign(self.dee, self.lead, self.trumpet)
        errors = self._apply(self._post(**{
            "existing-TOTAL": 0,
            "new-TOTAL": 1,
            "new-0-part": self.lead.pk,
            "new-0-instrument": self.trumpet.pk,
            "new-0-members": [self.ada.bandmember.pk],
        }))
        self.assertEqual(errors, [])
        self.assertTrue(PartAssignment.objects.filter(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet).exists())
        self.assertTrue(PartAssignment.objects.filter(member=self.dee.bandmember, song_part=self.lead, instrument=self.trumpet).exists())


class BulkEditPartAssignmentViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.band = Band.objects.get(slug="prsb")
        cls.song = Song.objects.create(title="Bulk Edit Page Song")
        BandSong.objects.create(band=cls.band, song=cls.song, in_gig_rotation=True)
        cls.lead = SongPart.objects.create(song=cls.song, name="Page Lead", has_solo=True)
        cls.pad = SongPart.objects.create(song=cls.song, name="Page Pad", has_solo=False)
        cls.trumpet = Instrument.objects.create(name="Page Trumpet", order=20)
        cls.trombone = Instrument.objects.create(name="Page Trombone", order=21)
        cls.ada = User.objects.create_user(username="bulk_page_ada", first_name="Ada", last_name="Page")
        cls.bea = User.objects.create_user(username="bulk_page_bea", first_name="Bea", last_name="Page")
        BandMembership.objects.create(band=cls.band, member=cls.ada.bandmember, is_active=True)
        BandMembership.objects.create(band=cls.band, member=cls.bea.bandmember, is_active=True)
        PartAssignment.objects.create(
            member=cls.ada.bandmember,
            song_part=cls.lead,
            instrument=cls.trumpet,
            performance_readiness=PerformanceReadiness.BACKUP,
            can_solo=True,
        )

    def setUp(self):
        self.url = reverse(
            "band:song_part_assignment_bulk_edit",
            kwargs={"slug": "prsb", "song_id": self.song.pk},
        )

    def test_song_detail_links_to_bulk_edit(self):
        detail = self.client.get(reverse("band:song_detail", kwargs={"slug": "prsb", "pk": self.song.pk}))
        self.assertContains(detail, "Add One Assignment")
        self.assertContains(detail, "Bulk Edit Assignments")
        self.assertContains(detail, self.url)

    def test_get_renders_every_part_and_existing_assignment(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertLess(body.index("Page Lead"), body.index("Page Pad"))
        self.assertContains(response, "Page Trumpet")
        self.assertContains(response, "Ada Page")
        self.assertContains(response, "Add row")
        self.assertNotContains(response, "Backup")
        self.assertNotContains(response, "solo")
        self.assertContains(response, 'name="existing-TOTAL"')
        self.assertContains(response, f'name="existing-0-members" value="{self.ada.bandmember.pk}"')

    def test_post_creates_and_redirects(self):
        response = self.client.post(self.url, {
            "existing-TOTAL": "1",
            "existing-0-part": str(self.lead.pk),
            "existing-0-instrument": str(self.trumpet.pk),
            "existing-0-members": str(self.ada.bandmember.pk),
            "new-TOTAL": "1",
            "new-0-part": str(self.pad.pk),
            "new-0-instrument": str(self.trombone.pk),
            "new-0-members": str(self.bea.bandmember.pk),
        })
        self.assertRedirects(response, reverse("band:song_detail", kwargs={"slug": "prsb", "pk": self.song.pk}))
        created = PartAssignment.objects.get(member=self.bea.bandmember, song_part=self.pad, instrument=self.trombone)
        self.assertEqual(created.performance_readiness, PerformanceReadiness.READY)
        self.assertFalse(created.can_solo)
        kept = PartAssignment.objects.get(member=self.ada.bandmember, song_part=self.lead, instrument=self.trumpet)
        self.assertEqual(kept.performance_readiness, PerformanceReadiness.BACKUP)
        self.assertTrue(kept.can_solo)

    def test_rejected_post_redisplays_chips_and_writes_nothing(self):
        before = set(PartAssignment.objects.values_list(
            "member_id", "song_part_id", "instrument_id", "performance_readiness", "can_solo",
        ))
        response = self.client.post(self.url, {
            "existing-TOTAL": "1",
            "existing-0-part": str(self.lead.pk),
            "existing-0-instrument": str(self.trumpet.pk),
            "existing-0-members": str(self.ada.bandmember.pk),
            "new-TOTAL": "2",
            "new-0-part": str(self.pad.pk),
            "new-0-instrument": str(self.trombone.pk),
            "new-0-members": str(self.ada.bandmember.pk),
            "new-1-part": str(self.pad.pk),
            "new-1-instrument": str(self.trombone.pk),
            "new-1-members": str(self.bea.bandmember.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Each instrument can only appear once on a part.")
        self.assertContains(response, "Ada Page")
        self.assertContains(response, "Bea Page")
        self.assertEqual(before, set(PartAssignment.objects.values_list(
            "member_id", "song_part_id", "instrument_id", "performance_readiness", "can_solo",
        )))

    def test_page_includes_picker_script(self):
        response = self.client.get(self.url)
        self.assertContains(response, "band/js/part_assignment_bulk_edit.js")


class GigAvailabilityBandGroupingTestCase(TestCase):
    def setUp(self):
        self.alpha = Band.objects.create(name="Alpha Band", slug="alpha-band")
        self.zulu = Band.objects.create(name="Zulu Band", slug="zulu-band")
        self.gig = Gig.objects.create(
            name="Two Band Gig",
            start_datetime=timezone.now() + timedelta(days=4),
            end_datetime=timezone.now() + timedelta(days=4, hours=2),
        )
        self.gig.bands.add(self.alpha, self.zulu)
        self.zoe = self._member("zoe_alpha", "Zoe", "Alpha", self.alpha)
        self.amy = self._member("amy_zulu", "Amy", "Zulu", self.zulu)
        self.mia = self._member("mia_both", "Mia", "Both", self.alpha)
        BandMembership.objects.create(band=self.zulu, member=self.mia)
        self.ned = self._member("ned_missing", "Ned", "Missing", self.alpha)
        self.uma = self._member("uma_zulu", "Uma", "Zulu", self.zulu)
        self.may = self._member("may_zulu", "May", "Zulu", self.zulu)
        GigAttendance.objects.create(gig=self.gig, member=self.zoe, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=self.gig, member=self.amy, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=self.gig, member=self.mia, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=self.gig, member=self.uma, status=GigAttendance.UNAVAILABLE)
        GigAttendance.objects.create(gig=self.gig, member=self.may, status=GigAttendance.MAYBE_AVAILABLE)

    def _member(self, username, first, last, band):
        user = User.objects.create_user(username=username, first_name=first, last_name=last)
        BandMembership.objects.create(band=band, member=user.bandmember)
        return user.bandmember

    def _detail(self):
        return self.client.get(reverse("band:gig_detail", kwargs={"slug": "alpha-band", "pk": self.gig.pk}))

    def _edit(self):
        return self.client.get(reverse(
            "band:gig_availability_update",
            kwargs={"slug": "alpha-band", "gig_id": self.gig.pk},
        ))

    def _availability_html(self, response):
        body = response.content.decode()
        return body.split("Band Member Availability", 1)[1].split("<h2>Setlist</h2>", 1)[0]

    def test_detail_groups_each_status_by_band(self):
        response = self._detail()
        self.assertTrue(response.context["show_band_groups"])
        self.assertEqual(
            [(group["label"], [str(member) for member in group["members"]]) for group in response.context["available_groups"]],
            [
                ("Alpha Band", ["Zoe Alpha"]),
                ("Alpha Band, Zulu Band", ["Mia Both"]),
                ("Zulu Band", ["Amy Zulu"]),
            ],
        )
        self.assertEqual(
            [(group["label"], [str(member) for member in group["members"]]) for group in response.context["maybe_available_groups"]],
            [("Zulu Band", ["May Zulu"])],
        )
        self.assertEqual(
            [(group["label"], [str(member) for member in group["members"]]) for group in response.context["unavailable_groups"]],
            [("Zulu Band", ["Uma Zulu"])],
        )
        self.assertEqual(
            [(group["label"], [str(member) for member in group["members"]]) for group in response.context["missing_groups"]],
            [("Alpha Band", ["Ned Missing"])],
        )
        self.assertEqual(len(response.context["available_members"]), 3)

        availability = self._availability_html(response)
        self.assertLess(availability.index(">Alpha Band<"), availability.index("Zoe Alpha"))
        self.assertLess(availability.index("Zoe Alpha"), availability.index(">Alpha Band, Zulu Band<"))
        self.assertLess(availability.index(">Alpha Band, Zulu Band<"), availability.index("Mia Both"))
        self.assertLess(availability.index("Mia Both"), availability.index(">Zulu Band<"))
        self.assertLess(availability.index(">Zulu Band<"), availability.index("Amy Zulu"))
        self.assertEqual(availability.count("Mia Both"), 1)

    def test_edit_form_groups_by_band_without_duplicating_a_shared_member(self):
        response = self._edit()
        self.assertTrue(response.context["show_band_groups"])
        grouped = [
            (group["label"], [form.member_name for form in group["forms"]])
            for group in response.context["form_groups"]
        ]
        self.assertEqual(grouped, [
            ("Alpha Band", ["Ned Missing", "Zoe Alpha"]),
            ("Alpha Band, Zulu Band", ["Mia Both"]),
            ("Zulu Band", ["Amy Zulu", "May Zulu", "Uma Zulu"]),
        ])
        self.assertEqual(response.context["formset"].total_form_count(), 6)
        body = response.content.decode()
        self.assertLess(body.index(">Alpha Band<"), body.index("Zoe Alpha"))
        self.assertLess(body.index("Zoe Alpha"), body.index(">Zulu Band<"))
        self.assertLess(body.index(">Zulu Band<"), body.index("Amy Zulu"))
        self.assertEqual(body.count("Mia Both"), 1)

    def test_single_band_gig_stays_a_flat_list(self):
        solo = Gig.objects.create(
            name="Solo Gig",
            start_datetime=timezone.now() + timedelta(days=5),
            end_datetime=timezone.now() + timedelta(days=5, hours=2),
        )
        solo.bands.add(self.alpha)
        GigAttendance.objects.create(gig=solo, member=self.zoe, status=GigAttendance.AVAILABLE)
        detail = self.client.get(reverse("band:gig_detail", kwargs={"slug": "alpha-band", "pk": solo.pk}))
        edit = self.client.get(reverse(
            "band:gig_availability_update",
            kwargs={"slug": "alpha-band", "gig_id": solo.pk},
        ))
        self.assertFalse(detail.context["show_band_groups"])
        self.assertFalse(edit.context["show_band_groups"])
        self.assertEqual(
            [str(member) for member in detail.context["available_groups"][0]["members"]],
            ["Zoe Alpha"],
        )
        self.assertNotContains(detail, 'class="band-name"')
        self.assertNotContains(edit, 'class="band-group"')
        self.assertContains(detail, "Zoe Alpha")
        self.assertContains(edit, "Zoe Alpha")

    def test_inactive_membership_still_groups_and_active_band_wins(self):
        ina = self._member("ina_old", "Ina", "Inactive", self.zulu)
        BandMembership.objects.filter(member=ina, band=self.zulu).update(is_active=False)
        pat = self._member("pat_partial", "Pat", "Partial", self.alpha)
        BandMembership.objects.create(band=self.zulu, member=pat, is_active=False)
        GigAttendance.objects.create(gig=self.gig, member=ina, status=GigAttendance.AVAILABLE)
        GigAttendance.objects.create(gig=self.gig, member=pat, status=GigAttendance.AVAILABLE)

        detail = self._detail()
        available = [
            (group["label"], [str(member) for member in group["members"]])
            for group in detail.context["available_groups"]
        ]
        self.assertEqual(available, [
            ("Alpha Band", ["Pat Partial", "Zoe Alpha"]),
            ("Alpha Band, Zulu Band", ["Mia Both"]),
            ("Zulu Band", ["Amy Zulu", "Ina Inactive"]),
        ])

    def test_saving_availability_still_updates_the_member(self):
        page = self._edit()
        formset = page.context["formset"]
        data = {
            "form-TOTAL_FORMS": str(formset.total_form_count()),
            "form-INITIAL_FORMS": str(formset.initial_form_count()),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for form in formset:
            data[form.add_prefix("attendance_id")] = form["attendance_id"].value() or ""
            data[form.add_prefix("member_id")] = form["member_id"].value()
            status = form["status"].value() or ""
            if form.member_name == "Amy Zulu":
                status = GigAttendance.UNAVAILABLE
            data[form.add_prefix("status")] = status

        response = self.client.post(reverse(
            "band:gig_availability_update",
            kwargs={"slug": "alpha-band", "gig_id": self.gig.pk},
        ), data)
        self.assertRedirects(response, reverse("band:gig_detail", kwargs={"slug": "alpha-band", "pk": self.gig.pk}))
        self.assertEqual(
            GigAttendance.objects.get(gig=self.gig, member=self.amy).status,
            GigAttendance.UNAVAILABLE,
        )

    def test_invalid_post_redisplays_names_inside_band_groups(self):
        page = self._edit()
        formset = page.context["formset"]
        data = {
            "form-TOTAL_FORMS": str(formset.total_form_count()),
            "form-INITIAL_FORMS": str(formset.initial_form_count()),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for form in formset:
            data[form.add_prefix("attendance_id")] = form["attendance_id"].value() or ""
            data[form.add_prefix("member_id")] = form["member_id"].value()
            status = form["status"].value() or ""
            if form.member_name == "Amy Zulu":
                status = "bogus"
            data[form.add_prefix("status")] = status

        response = self.client.post(reverse(
            "band:gig_availability_update",
            kwargs={"slug": "alpha-band", "gig_id": self.gig.pk},
        ), data)
        self.assertEqual(response.status_code, 200)
        grouped = [
            (group["label"], [form.member_name for form in group["forms"]])
            for group in response.context["form_groups"]
        ]
        self.assertIn(("Zulu Band", ["Amy Zulu", "May Zulu", "Uma Zulu"]), grouped)
        self.assertContains(response, "Amy Zulu")


class SetlistStaffingNoteTestCase(TestCase):
    def test_staffing_note_is_blank_without_a_multi_band_mode(self):
        gig = Gig.objects.create(
            name="Plain Gig",
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=2),
        )
        song = Song.objects.create(title="Plain Song")
        entry = GigSetlistEntry(gig=gig, song=song, staffing_mode="", staffing_band=None)
        self.assertEqual(entry.staffing_note, "")
        entry.staffing_mode = "only"
        self.assertEqual(entry.staffing_note, "")

    def test_pages_show_only_prefer_and_any_band(self):
        prsb = Band.objects.get(slug=PRSB_SLUG)
        pan = Band.objects.create(name="Pan Ultimate", slug="pan-ultimate")
        gig = Gig.objects.create(
            name="Staffed Gig",
            start_datetime=timezone.now() + timedelta(days=2),
            end_datetime=timezone.now() + timedelta(days=2, hours=2),
        )
        gig.bands.add(prsb, pan)
        member = User.objects.create_user(
            username="staff_player", first_name="Pat", last_name="Player",
        ).bandmember
        BandMembership.objects.create(band=prsb, member=member)
        BandMembership.objects.create(band=pan, member=member)
        GigAttendance.objects.create(gig=gig, member=member, status=GigAttendance.AVAILABLE)
        horn = Instrument.objects.create(name="Staff Horn", quantity=1, order=0)
        GigInstrument.objects.create(gig=gig, instrument=horn, gig_quantity=1)

        def add_song(title, mode, band, order):
            song = Song.objects.create(title=title, duration=timedelta(minutes=3))
            for gig_band in (prsb, pan):
                BandSong.objects.create(band=gig_band, song=song, is_active=True, in_gig_rotation=True)
            part = SongPart.objects.create(song=song, name="Lead")
            PartAssignment.objects.create(
                member=member, song_part=part, instrument=horn,
                performance_readiness=PerformanceReadiness.READY,
            )
            return GigSetlistEntry.objects.create(
                gig=gig, song=song, staffing_mode=mode, staffing_band=band, _order=order,
            )

        add_song("Jammin'", "only", prsb, 0)
        add_song("Sarah", "prefer", pan, 1)
        add_song("Jump in da Line", "any", None, 2)
        GigSetlistEntry.objects.create(gig=gig, break_duration=timedelta(minutes=5), _order=3)
        extra = Song.objects.create(title="Extra Tune", duration=timedelta(minutes=2))
        BandSong.objects.create(band=prsb, song=extra, is_active=True, in_gig_rotation=True)
        extra_part = SongPart.objects.create(song=extra, name="Lead")
        PartAssignment.objects.create(
            member=member, song_part=extra_part, instrument=horn,
            performance_readiness=PerformanceReadiness.READY,
        )

        detail = self.client.get(reverse("band:gig_detail", kwargs={"slug": PRSB_SLUG, "pk": gig.pk}))
        setlist_html = html.unescape(
            detail.content.decode().split("<h2>Setlist</h2>", 1)[1].split("<h2>Instruments</h2>", 1)[0]
        )
        self.assertIn("Jammin' (Only PRSB) (3m 0s)", setlist_html)
        self.assertIn("Sarah (Prefer Pan Ultimate) (3m 0s)", setlist_html)
        self.assertIn("Jump in da Line (Any Band) (3m 0s)", setlist_html)
        self.assertIn("Break (5m 0s)", setlist_html)
        self.assertNotIn("Break (Only", setlist_html)
        self.assertNotIn("Break (Prefer", setlist_html)
        self.assertNotIn("Break (Any Band)", setlist_html)

        assignments = self.client.get(reverse(
            "band:gig_part_assignments_detail", kwargs={"slug": PRSB_SLUG, "pk": gig.pk},
        ))
        body = html.unescape(assignments.content.decode())
        setlist_songs, recommendations = body.split("<h2>Song Recommendations</h2>", 1)
        self.assertIn("Jammin' (Only PRSB)", setlist_songs)
        self.assertIn("Sarah (Prefer Pan Ultimate)", setlist_songs)
        self.assertIn("Jump in da Line (Any Band)", setlist_songs)
        self.assertIn("Extra Tune", recommendations)
        self.assertNotIn("(Only ", recommendations)
        self.assertNotIn("(Prefer ", recommendations)
        self.assertNotIn("(Any Band)", recommendations)

        printed = self.client.get(
            reverse("band:gig_part_assignment_print", kwargs={"slug": PRSB_SLUG, "pk": gig.pk}),
            {"order": "setlist"},
        )
        headings = re.findall(r"<h2>(.*?)</h2>", html.unescape(printed.content.decode()))
        self.assertEqual(headings, [
            "Jammin' (Only PRSB)",
            "Sarah (Prefer Pan Ultimate)",
            "Jump in da Line (Any Band)",
        ])

