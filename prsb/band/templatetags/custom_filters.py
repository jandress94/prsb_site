#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# custom_filters.py
# Created: 2/25/25

from django import template

register = template.Library()


@register.filter
def duration_to_minutes_seconds(duration):
    if duration is None:
        return "0m 0s"

    total_seconds = int(duration.total_seconds())
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}m {seconds}s"
