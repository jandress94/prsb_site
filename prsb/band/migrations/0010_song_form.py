# Generated by Django 5.0.4 on 2024-09-14 00:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0009_gig_alter_bandmember_bio_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='song',
            name='form',
            field=models.CharField(blank=True, max_length=256),
        ),
    ]
