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
from band.models import BandMember, BandSpecialDate

BIRTHDAY_EMAIL_RECIPIENTS = [
    "jim.andress+prsb@gmail.com"
]


def lambda_handler(event, context):
    d = timezone.localtime(timezone.now()).date()

    print("Today's date:", d)

    results = [f"{m.user.get_full_name()}'s Birthday" for m in BandMember.objects.filter(birthday=d)]
    results += [bsd.description for bsd in BandSpecialDate.objects.filter(date=d)]

    if len(results) > 0:
        result_formatted = '\n'.join(results)
        print(result_formatted)

        send_mail(
            f"PRSB Special Day {d}!",
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
