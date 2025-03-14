# Generated by Django 5.1.1 on 2025-02-20 00:59

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0019_gig_start_datetime'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='gig',
            options={'ordering': ['-start_datetime']},
        ),
        migrations.AlterModelOptions(
            name='instrument',
            options={'ordering': ('order',)},
        ),
        migrations.AddField(
            model_name='instrument',
            name='order',
            field=models.PositiveIntegerField(db_index=True, default=1, editable=False, verbose_name='order'),
            preserve_default=False,
        ),
    ]
