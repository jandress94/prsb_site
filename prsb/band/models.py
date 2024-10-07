from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from phonenumber_field.modelfields import PhoneNumberField
from django.contrib.auth.models import User


class Song(models.Model):
    title = models.CharField(max_length=256)
    composer = models.CharField(max_length=256, blank=True)
    duration = models.DurationField(null=True, blank=True)
    in_gig_rotation = models.BooleanField(default=False)
    form = models.CharField(max_length=256, blank=True)

    def __str__(self):
        return self.title


class SongPart(models.Model):
    song = models.ForeignKey(Song, related_name='parts', on_delete=models.CASCADE)
    name = models.CharField(max_length=256)

    class Meta:
        order_with_respect_to = 'song'

    def __str__(self):
        return f'{self.song.title}: {self.name}'


class BandMember(models.Model):
    user = models.OneToOneField(User, primary_key=True, on_delete=models.CASCADE)

    phone_number = PhoneNumberField(null=True, blank=True)

    emergency_contact_name = models.CharField(max_length=256, blank=True)
    emergency_contact_phone = PhoneNumberField(null=True, blank=True)

    bio = models.TextField(blank=True)
    birthday = models.DateField(null=True, blank=True)
    dietary_restrictions = models.TextField(blank=True)
    tshirt_size = models.CharField(max_length=256, blank=True)

    def __str__(self):
        return self.user.get_full_name()


@receiver(post_save, sender=User)
def update_profile_signal(sender, instance, created, **kwargs):
    if created:
        BandMember.objects.create(user=instance)
    instance.bandmember.save()


class Instrument(models.Model):
    name = models.CharField(max_length=256)
    quantity = models.PositiveSmallIntegerField(default=1)

    def __str__(self):
        return self.name


class PartAssignment(models.Model):
    member = models.ForeignKey(BandMember, on_delete=models.CASCADE)
    song_part = models.ForeignKey(SongPart, on_delete=models.CASCADE)
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE)

    def __str__(self):
        return f'{self.member} plays {self.instrument} on {self.song_part}'


class Gig(models.Model):
    name = models.CharField(max_length=256)

    def __str__(self):
        return self.name


class GigInstrument(models.Model):
    gig = models.ForeignKey(Gig, on_delete=models.CASCADE)
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE)
    gig_quantity = models.PositiveSmallIntegerField(default=1)

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

    def __str__(self):
        return f'{self.gig}: {self.member} ({self.status})'