# Generated by Django 5.0.4 on 2024-05-03 06:09

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Song',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=256)),
                ('author', models.CharField(max_length=256)),
                ('duration', models.DurationField()),
            ],
        ),
    ]
