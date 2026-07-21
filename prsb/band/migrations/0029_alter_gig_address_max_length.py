# Generated manually for SQLite test compatibility / fix missing max_length

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0028_gigpartassignmentoverride_override_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='gig',
            name='address',
            field=models.CharField(blank=True, max_length=512),
        ),
    ]
