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
from types import SimpleNamespace

from scripts.gig_part_assignment import get_gig_part_assignments, get_max_instrument_usage
from scripts.coverage_risk import get_coverage_risk, DEFAULT_LOOKBACK
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
