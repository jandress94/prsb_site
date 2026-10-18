#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# email_test.py
# Created: 3/7/25

import django

django.setup()


from django.core.mail import send_mail
from django.utils import timezone
from django.db import connections
from prsb.settings import DEFAULT_FROM_EMAIL
from band.models import Band, BandMember, BandSpecialDate

BIRTHDAY_EMAIL_RECIPIENTS = [
    "jim.andress+prsb@gmail.com"
]


def lambda_handler(event, context):
    d = timezone.localtime(timezone.now()).date()

    print("Today's date:", d)

    sections = []
    bands_with_results = []

    for band in Band.objects.filter(is_active=True).order_by("name"):
        lines = []
        for member in (
            BandMember.objects.filter(
                birthday=d,
                bandmembership__band=band,
                bandmembership__is_active=True,
            )
            .select_related("user")
            .order_by("user__first_name", "user__last_name")
            .distinct()
        ):
            lines.append(f"{member.user.get_full_name()}'s Birthday")
        for special in BandSpecialDate.objects.filter(band=band, date=d):
            lines.append(special.description)
        if lines:
            bands_with_results.append(band)
            sections.append("\n".join([band.name, *lines]))

    if sections:
        result_formatted = "\n\n".join(sections)
        print(result_formatted)

        if len(bands_with_results) == 1:
            subject = f"Special Day {d} {bands_with_results[0].name}"
        else:
            subject = f"Special Day {d}"

        send_mail(
            subject,
            f"Today is a special day!\n\n{result_formatted}",
            DEFAULT_FROM_EMAIL,
            BIRTHDAY_EMAIL_RECIPIENTS,
            fail_silently=False,
        )
    else:
        print('Not a special day today.')

    connections.close_all()

    return {
        "statusCode": 200,
        "body": "Success"
    }


if __name__ == "__main__":
    lambda_handler(None, None)
