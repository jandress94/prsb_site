# Generated by Django 5.0.4 on 2024-05-03 06:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0004_alter_song_author'),
    ]

    operations = [
        migrations.AlterField(
            model_name='song',
            name='author',
            field=models.CharField(default='', max_length=256),
        ),
    ]