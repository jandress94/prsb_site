# Generated by Django 5.1.1 on 2025-02-21 07:04

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0020_alter_gig_options_alter_instrument_options_and_more'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='giginstrument',
            options={'ordering': ['instrument__order']},
        ),
        migrations.CreateModel(
            name='GigSetlistEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('break_duration', models.DurationField(blank=True, null=True)),
                ('gig', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='band.gig')),
                ('song', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='band.song')),
            ],
            options={
                'order_with_respect_to': 'gig',
            },
        ),
    ]
