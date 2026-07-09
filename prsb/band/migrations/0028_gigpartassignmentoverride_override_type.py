from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0027_instrument_include_in_gig_song_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='gigpartassignmentoverride',
            name='override_type',
            field=models.CharField(
                choices=[('assign', 'Assign to Part'), ('not_playing', 'Not Playing')],
                default='assign',
                max_length=256,
            ),
        ),
        migrations.AddConstraint(
            model_name='gigpartassignmentoverride',
            constraint=models.UniqueConstraint(
                fields=('member', 'song_part', 'gig_instrument'),
                name='unique_gig_part_assignment_override',
            ),
        ),
    ]
