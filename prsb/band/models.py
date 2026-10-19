from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from djangoyearlessdate.models import YearlessDateField
from phonenumber_field.modelfields import PhoneNumberField
from django.contrib.auth.models import User
from ordered_model.models import OrderedModel
from tinymce import models as tinymce_models


class Band(models.Model):
    name = models.CharField(max_length=256)
    slug = models.SlugField(max_length=256, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        if self.gig_set.exists() or GigSetlistEntry.objects.filter(staffing_band=self).exists():
            raise ValidationError("Deactivate this band instead of deleting it while gigs still use it.")
        return super().delete(*args, **kwargs)


class Song(models.Model):
    title = models.CharField(max_length=256)
    composer = models.CharField(max_length=256, blank=True)
    duration = models.DurationField(null=True, blank=True)
    form = models.CharField(max_length=256, blank=True)

    class Meta:
        ordering = ["title"]

    def _band_for_absolute_url(self):
        band_songs = self.band_songs.select_related("band").order_by("band__name")
        active = band_songs.filter(is_active=True).first()
        if active is not None:
            return active.band
        linked = band_songs.first()
        return linked.band if linked is not None else None

    def get_absolute_url(self):
        band = self._band_for_absolute_url()
        if band is None:
            raise ValueError("Song has no band link; cannot build a band-scoped URL.")
        return reverse("band:song_detail", kwargs={"slug": band.slug, "pk": self.pk})

    def __str__(self):
        return self.title


class BandSong(models.Model):
    band = models.ForeignKey(Band, on_delete=models.CASCADE)
    song = models.ForeignKey(Song, related_name="band_songs", on_delete=models.CASCADE)
    in_gig_rotation = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["band", "song"], name="unique_band_song")
        ]


class SongPart(models.Model):
    song = models.ForeignKey(Song, related_name='parts', on_delete=models.CASCADE)
    name = models.CharField(max_length=256)
    has_solo = models.BooleanField(default=False)

    class Meta:
        order_with_respect_to = 'song'

    def __str__(self):
        return f'{self.song.title}: {self.name}'

    def get_order(self) -> int:
        return self._order

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.has_solo:
            PartAssignment.objects.filter(song_part=self, can_solo=True).update(can_solo=False)


class BandMember(models.Model):
    user = models.OneToOneField(User, primary_key=True, on_delete=models.CASCADE)

    phone_number = PhoneNumberField(null=True, blank=True)

    emergency_contact_name = models.CharField(max_length=256, blank=True)
    emergency_contact_phone = PhoneNumberField(null=True, blank=True)

    bio = tinymce_models.HTMLField(blank=True)
    birthday = YearlessDateField(null=True, blank=True)
    dietary_restrictions = models.TextField(blank=True)
    tshirt_size = models.CharField(max_length=256, blank=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name"]

    def __str__(self):
        return self.user.get_full_name()


class BandMembership(models.Model):
    band = models.ForeignKey(Band, on_delete=models.CASCADE)
    member = models.ForeignKey(BandMember, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["band", "member"], name="unique_band_membership")
        ]


@receiver(post_save, sender=User)
def update_profile_signal(sender, instance, created, **kwargs):
    if created:
        BandMember.objects.create(user=instance)
    instance.bandmember.save()


class DrumKitCoverPlayer(models.Model):
    band = models.ForeignKey(Band, on_delete=models.CASCADE)
    member = models.ForeignKey(BandMember, on_delete=models.CASCADE)
    priority = models.PositiveSmallIntegerField(
        help_text="Lower number = preferred cover (e.g. 1 before 2).",
    )

    class Meta:
        ordering = ["priority", "member__user__first_name", "member__user__last_name"]
        constraints = [
            models.UniqueConstraint(fields=["band", "member"], name="unique_drum_kit_cover_player")
        ]

    def __str__(self):
        return f"{self.member} (priority {self.priority})"


class Instrument(OrderedModel):
    name = models.CharField(max_length=256)
    quantity = models.PositiveSmallIntegerField(default=1)
    include_in_gig_song_count = models.BooleanField(default=True)
    include_in_coverage_risk = models.BooleanField(
        default=True,
        help_text="If unchecked, assignments on this instrument are ignored by the Coverage Risk report "
                  "(e.g. Drumset / Engine Room, where any drummer can cover).",
    )
    is_drum_kit_cover_instrument = models.BooleanField(
        default=False,
        help_text="If checked, this instrument is the target for drum-kit cover fallbacks "
                  "(expect exactly one instrument flagged).",
    )

    def __str__(self):
        return self.name


class PerformanceReadiness:
    READY = "ready"
    BACKUP = "backup"
    NOT_READY = "not_ready"
    CHOICES = {
        READY: "Ready to Perform",
        BACKUP: "Can Perform as Backup",
        NOT_READY: "Not Ready to Perform"
    }
    CHOICES_OVERRIDES = {
        READY: "Ready to Perform",
        BACKUP: "Can Perform as Backup"
    }


class OverrideType:
    ASSIGN = "assign"
    NOT_PLAYING = "not_playing"
    CHOICES = {
        ASSIGN: "Assign to Part",
        NOT_PLAYING: "Not Playing",
    }


class PartAssignment(models.Model):
    member = models.ForeignKey(BandMember, on_delete=models.CASCADE)
    song_part = models.ForeignKey(SongPart, on_delete=models.CASCADE)
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE)
    performance_readiness = models.CharField(max_length=256,
                                             choices=PerformanceReadiness.CHOICES,
                                             default=PerformanceReadiness.READY)
    can_solo = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['member', 'song_part', 'instrument'], name='unique PartAssignment')
        ]

    def __str__(self):
        return f'{self.member} plays {self.instrument} on {self.song_part}'

    def is_backup(self) -> bool:
        return self.performance_readiness == PerformanceReadiness.BACKUP

    def is_not_ready(self) -> bool:
        return self.performance_readiness == PerformanceReadiness.NOT_READY

    def clean(self):
        super().clean()
        if self.can_solo and self.song_part_id and not self.song_part.has_solo:
            raise ValidationError({"can_solo": "Can only solo on a part that has a solo."})


class Gig(models.Model):
    name = models.CharField(max_length=256)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()

    address = models.CharField(max_length=512, blank=True)

    notes = tinymce_models.HTMLField(blank=True)

    include_in_stats = models.BooleanField(
        default=True,
        help_text="Include this gig in coverage-risk attendance stats.",
    )

    bands = models.ManyToManyField("Band", related_name="gig_set", blank=True)

    def clean(self):
        super().clean()
        if self.end_datetime <= self.start_datetime:
            raise ValidationError("A gig cannot end before it begins.")

    class Meta:
        ordering = ["-start_datetime"]

    def __str__(self):
        return self.name


class GigSetlistEntry(models.Model):
    gig = models.ForeignKey(Gig, on_delete=models.CASCADE)

    song = models.ForeignKey(Song, on_delete=models.CASCADE, null=True, blank=True)
    break_duration = models.DurationField(null=True, blank=True)
    staffing_mode = models.CharField(max_length=16, blank=True, default="")
    staffing_band = models.ForeignKey(
        "Band", null=True, blank=True, on_delete=models.PROTECT, related_name="staffed_entries",
    )

    @property
    def is_break(self) -> bool:
        return self.song is None

    @property
    def staffing_note(self) -> str:
        if self.staffing_mode == "only" and self.staffing_band_id:
            label = f"Only {self.staffing_band}"
        elif self.staffing_mode == "prefer" and self.staffing_band_id:
            label = f"Prefer {self.staffing_band}"
        elif self.staffing_mode == "any":
            label = "Any Band"
        else:
            return ""
        return f" ({label})"

    def clean(self):
        super().clean()
        if self.song is not None and self.break_duration is not None:
            raise ValidationError("A setlist entry should be either a song or a break, not both")
        if self.song is None and self.break_duration is None:
            raise ValidationError("A setlist entry needs to be either a song or a break")

    class Meta:
        order_with_respect_to = 'gig'


class GigInstrument(models.Model):
    gig = models.ForeignKey(Gig, on_delete=models.CASCADE)
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE)
    gig_quantity = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['gig', 'instrument'], name='unique GigInstrument', violation_error_message='The instrument is already assigned to this gig.')
        ]
        ordering = ["instrument__order"]

    def __str__(self):
        return f'{self.gig}: {self.instrument}'


class GigAttendance(models.Model):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    MAYBE_AVAILABLE = "maybe_available"
    AVAILABILITY_CHOICES = {
        AVAILABLE: "Available",
        MAYBE_AVAILABLE: "Maybe Available",
        UNAVAILABLE: "Unavailable"
    }

    gig = models.ForeignKey(Gig, on_delete=models.CASCADE)
    member = models.ForeignKey(BandMember, on_delete=models.CASCADE)
    status = models.CharField(max_length=256, choices=AVAILABILITY_CHOICES)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['gig', 'member'], name='unique GigAttendance')
        ]

    def __str__(self):
        return f'{self.gig}: {self.member} ({self.status})'


class GigPartAssignmentOverride(models.Model):
    member = models.ForeignKey(BandMember, on_delete=models.CASCADE)
    song_part = models.ForeignKey(SongPart, on_delete=models.CASCADE)
    gig_instrument = models.ForeignKey(GigInstrument, on_delete=models.CASCADE)
    override_type = models.CharField(max_length=256,
                                     choices=OverrideType.CHOICES,
                                     default=OverrideType.ASSIGN)
    performance_readiness = models.CharField(max_length=256,
                                             choices=PerformanceReadiness.CHOICES_OVERRIDES,
                                             default=PerformanceReadiness.READY)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['member', 'song_part', 'gig_instrument'],
                name='unique_gig_part_assignment_override',
            ),
        ]

    def __str__(self):
        if self.override_type == OverrideType.NOT_PLAYING:
            return (f'{self.member} does not play {self.gig_instrument.instrument} on '
                    f'{self.song_part} at {self.gig_instrument.gig}')
        return f'{self.member} plays {self.gig_instrument.instrument} on {self.song_part} at {self.gig_instrument.gig}'

    def clean(self):
        super().clean()
        if self.override_type == OverrideType.ASSIGN and GigPartAssignmentOverride.objects.filter(
                member=self.member,
                gig_instrument__gig=self.gig_instrument.gig,
                song_part__song=self.song_part.song,
                override_type=OverrideType.ASSIGN,
        ).exclude(pk=self.pk).exists():
            raise ValidationError("A band member can only have one assign override per song per gig")


class BandSpecialDate(models.Model):
    band = models.ForeignKey(Band, on_delete=models.CASCADE)
    date = YearlessDateField()
    description = models.TextField()

    def __str__(self):
        return f'{self.date}: {self.description}'

    class Meta:
        ordering = ['date']
