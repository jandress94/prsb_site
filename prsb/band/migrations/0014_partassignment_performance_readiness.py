# Generated by Django 5.1.1 on 2025-02-12 16:33

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0013_alter_bandmember_options_alter_song_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='partassignment',
            name='performance_readiness',
            field=models.CharField(choices=[('ready', 'Ready to Perform'), ('backup', 'Can Perform as Backup'), ('not_ready', 'Not Ready to Perform')], default='ready', max_length=256),
        ),
    ]
