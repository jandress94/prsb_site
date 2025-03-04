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


class Song(models.Model):
    title = models.CharField(max_length=256)
    composer = models.CharField(max_length=256, blank=True)
    duration = models.DurationField(null=True, blank=True)
    in_gig_rotation = models.BooleanField(default=False)
    form = models.CharField(max_length=256, blank=True)

    class Meta:
        ordering = ["title"]

    def get_absolute_url(self):
        return reverse("band:song_detail", kwargs={"pk": self.pk})

    def __str__(self):
        return self.title


class SongPart(models.Model):
    song = models.ForeignKey(Song, related_name='parts', on_delete=models.CASCADE)
    name = models.CharField(max_length=256)

    class Meta:
        order_with_respect_to = 'song'

    def __str__(self):
        return f'{self.song.title}: {self.name}'

    def get_order(self) -> int:
        return self._order


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


@receiver(post_save, sender=User)
def update_profile_signal(sender, instance, created, **kwargs):
    if created:
        BandMember.objects.create(user=instance)
    instance.bandmember.save()


class Instrument(OrderedModel):
    name = models.CharField(max_length=256)
    quantity = models.PositiveSmallIntegerField(default=1)

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


class PartAssignment(models.Model):
    member = models.ForeignKey(BandMember, on_delete=models.CASCADE)
    song_part = models.ForeignKey(SongPart, on_delete=models.CASCADE)
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE)
    performance_readiness = models.CharField(max_length=256,
                                             choices=PerformanceReadiness.CHOICES,
                                             default=PerformanceReadiness.READY)

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


class Gig(models.Model):
    name = models.CharField(max_length=256)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()

    address = models.CharField(blank=True)

    notes = tinymce_models.HTMLField(blank=True)

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

    @property
    def is_break(self) -> bool:
        return self.song is None

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
            models.UniqueConstraint(fields=['gig', 'instrument'], name='unique GigInstrument')
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
        UNAVAILABLE: "Unavailable",
        MAYBE_AVAILABLE: "Maybe Available"
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
    performance_readiness = models.CharField(max_length=256,
                                             choices=PerformanceReadiness.CHOICES_OVERRIDES,
                                             default=PerformanceReadiness.READY)

    def __str__(self):
        return f'{self.member} plays {self.gig_instrument.instrument} on {self.song_part} at {self.gig_instrument.gig}'

    def clean(self):
        super().clean()
        if GigPartAssignmentOverride.objects.filter(member=self.member,
                                                    gig_instrument__gig=self.gig_instrument.gig,
                                                    song_part__song=self.song_part.song).exclude(pk=self.pk).exists():
            raise ValidationError("A band member can only have one override per song per gig")
