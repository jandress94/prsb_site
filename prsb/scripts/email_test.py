#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# email_test.py
# Created: 3/7/25

import django

django.setup()

from django.core.mail import send_mail


def main():
    send_mail(
        "Test Subject",
        "This is a test email from Django.",
        "phinneyridgesteelband@gmail.com",
        ["jim.andress+prsb@gmail.com"],
        fail_silently=False,
    )


if __name__ == "__main__":
    main()
