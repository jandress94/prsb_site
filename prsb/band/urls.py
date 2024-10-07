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

    path("members/", views.MemberListView.as_view(), name="member_list"),
    path("members/<int:pk>/", views.MemberDetailView.as_view(), name="member_detail"),
    path("members/<int:pk>/update/", views.MemberUpdateView.as_view(), name="member_update"),

    path("songs/", views.SongListView.as_view(), name="song_list"),
    path("songs/<int:pk>/", views.SongDetailView.as_view(), name="song_detail"),

    # path("part-assignments/", views.PartAssignmentListView.as_view(), name="part_assignment_list"),

    path("gigs/", views.GigListView.as_view(), name="gig_list"),
    path("gigs/<int:pk>/", views.GigDetailView.as_view(), name="gig_detail"),

    path("instruments/", views.InstrumentListView.as_view(), name="instrument_list"),
]
