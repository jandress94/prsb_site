# Generated by Django 5.0.4 on 2024-09-21 00:19

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0011_alter_bandmember_emergency_contact_phone'),
    ]

    operations = [
        migrations.CreateModel(
            name='GigInstrument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gig_quantity', models.PositiveSmallIntegerField(default=1)),
                ('gig', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='band.gig')),
                ('instrument', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='band.instrument')),
            ],
        ),
    ]