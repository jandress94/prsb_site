#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# urls.py
# Author: Jim Andress
# Created: 4/28/24

from django.urls import path

from . import views

app_name = "band"
urlpatterns = [
    path("", views.index, name='index'),
    path("songs/", views.SongListView.as_view(), name="song_list"),
    path("songs/<int:pk>/", views.SongDetailView.as_view(), name="song_detail")
]
