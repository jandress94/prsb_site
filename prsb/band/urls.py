#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# urls.py
# Author: Jim Andress
# Created: 4/28/24

from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name='index')
]
