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
from band.models import BandMember


BIRTHDAY_EMAIL_RECIPIENTS = [
    "jim.andress+prsb@gmail.com"
]


def lambda_handler(event, context):
    d = timezone.localtime(timezone.now()).date()

    print("Today's date:", d)

    today_birthdays = [m.user.get_full_name() for m in BandMember.objects.filter(birthday=d)]

    if len(today_birthdays) > 0:
        birthday_list = '\n'.join(today_birthdays)
        print(f"Birthdays today!")
        print(birthday_list)

        send_mail(
            f"PRSB Birthdays {d}!",
            f"The following band members have their birthday today!\n\n{birthday_list}",
            DEFAULT_FROM_EMAIL,
            BIRTHDAY_EMAIL_RECIPIENTS,
            fail_silently=False,
        )
    else:
        print('No birthdays today.')

    connections.close_all()

    return {
        "statusCode": 200,
        "body": "Success"
    }


if __name__ == "__main__":
    lambda_handler(None, None)
