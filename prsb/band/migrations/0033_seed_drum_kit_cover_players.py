from django.db import migrations


def forwards(apps, schema_editor):
    Instrument = apps.get_model("band", "Instrument")
    BandMember = apps.get_model("band", "BandMember")
    DrumKitCoverPlayer = apps.get_model("band", "DrumKitCoverPlayer")
    User = apps.get_model("auth", "User")

    Instrument.objects.filter(name="Drum Set").update(is_drum_kit_cover_instrument=True)

    priorities = {
        ("Jansen", "Leggett"): 1,
        ("Ben", "Hills"): 2,
        ("Carlos", "Fresnillo"): 2,
    }
    for (first, last), priority in priorities.items():
        user = User.objects.filter(first_name=first, last_name=last).first()
        if user is None:
            continue
        member = BandMember.objects.filter(pk=user.pk).first()
        if member is None:
            continue
        DrumKitCoverPlayer.objects.update_or_create(
            member=member, defaults={"priority": priority},
        )


def backwards(apps, schema_editor):
    Instrument = apps.get_model("band", "Instrument")
    BandMember = apps.get_model("band", "BandMember")
    DrumKitCoverPlayer = apps.get_model("band", "DrumKitCoverPlayer")
    User = apps.get_model("auth", "User")

    Instrument.objects.filter(name="Drum Set").update(is_drum_kit_cover_instrument=False)

    priorities = {
        ("Jansen", "Leggett"): 1,
        ("Ben", "Hills"): 2,
        ("Carlos", "Fresnillo"): 2,
    }
    for (first, last), priority in priorities.items():
        user = User.objects.filter(first_name=first, last_name=last).first()
        if user is None:
            continue
        member = BandMember.objects.filter(pk=user.pk).first()
        if member is None:
            continue
        DrumKitCoverPlayer.objects.filter(member=member, priority=priority).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0032_instrument_is_drum_kit_cover_instrument_drumkitcoverplayer'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
