# Generated by Django 5.0.4 on 2024-09-17 19:29

import phonenumber_field.modelfields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0010_song_form'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bandmember',
            name='emergency_contact_phone',
            field=phonenumber_field.modelfields.PhoneNumberField(blank=True, max_length=128, null=True, region=None),
        ),
    ]
