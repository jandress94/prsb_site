# Generated by Django 5.1.1 on 2025-02-23 04:35

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0022_gig_address_gig_end_datetime_gig_notes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='gig',
            name='address',
            field=models.CharField(blank=True),
        ),
    ]
