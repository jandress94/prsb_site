from django.db import migrations, models


def negate_include_in_stats(apps, schema_editor):
    # The old flag meant "exclude this gig"; this one means the opposite.
    Gig = apps.get_model("band", "Gig")
    excluded_ids = list(Gig.objects.filter(include_in_stats=True).values_list("pk", flat=True))
    Gig.objects.filter(include_in_stats=False).update(include_in_stats=True)
    Gig.objects.filter(pk__in=excluded_ids).update(include_in_stats=False)


class Migration(migrations.Migration):

    dependencies = [
        ("band", "0035_remove_song_in_gig_rotation"),
    ]

    operations = [
        migrations.RenameField(
            model_name="gig",
            old_name="is_small_group",
            new_name="include_in_stats",
        ),
        migrations.RunPython(negate_include_in_stats, negate_include_in_stats),
        migrations.AlterField(
            model_name="gig",
            name="include_in_stats",
            field=models.BooleanField(
                default=True,
                help_text="Include this gig in coverage-risk attendance stats.",
            ),
        ),
    ]
