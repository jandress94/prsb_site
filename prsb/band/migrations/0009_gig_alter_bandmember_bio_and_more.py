# Generated by Django 5.0.4 on 2024-09-13 23:43

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0008_bandmember_instrument_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Gig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=256)),
            ],
        ),
        migrations.AlterField(
            model_name='bandmember',
            name='bio',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='bandmember',
            name='dietary_restrictions',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='bandmember',
            name='emergency_contact_name',
            field=models.CharField(blank=True, default='', max_length=256),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='bandmember',
            name='emergency_contact_phone',
            field=models.CharField(blank=True, default='', max_length=256),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='bandmember',
            name='tshirt_size',
            field=models.CharField(blank=True, default='', max_length=256),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='song',
            name='composer',
            field=models.CharField(blank=True, default='', max_length=256),
            preserve_default=False,
        ),
        migrations.CreateModel(
            name='GigAttendance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('available', 'Available'), ('unavailable', 'Unavailable'), ('maybe_available', 'Maybe Available')], max_length=256)),
                ('gig', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='band.gig')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='band.bandmember')),
            ],
        ),
    ]