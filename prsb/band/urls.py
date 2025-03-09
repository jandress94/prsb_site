#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# urls.py
# Author: Jim Andress
# Created: 4/28/24
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

app_name = "band"
urlpatterns = [
    path("accounts/login/", LoginView.as_view(template_name="band/registration/login.html")),
    path("accounts/logout/", LogoutView.as_view(next_page="login"), name="logout"),

    path("", views.index, name='index'),

    path("members/", views.MemberListView.as_view(), name="member_list"),
    path("members/<int:pk>/", views.MemberDetailView.as_view(), name="member_detail"),
    # path("members/<int:pk>/update/", views.MemberUpdateView.as_view(), name="member_update"),
    path("members/<int:member_id>/part-assignments/create/", views.MemberPartAssignmentCreateView.as_view(), name='member_part_assignment_create'),
    path("members/<int:member_id>/part-assignments/<int:pk>/update/",
         views.MemberPartAssignmentUpdateView.as_view(), name="member_part_assignment_update"),

    path("songs/", views.SongListView.as_view(), name="song_list"),
    path("songs/create/", views.SongCreateView.as_view(), name="song_create"),
    path("songs/<int:pk>/", views.SongDetailView.as_view(), name="song_detail"),
    path("songs/<int:pk>/update/", views.SongUpdateView.as_view(), name="song_update"),
    path("songs/<int:song_id>/part-assignments/create/", views.SongPartAssignmentCreateView.as_view(), name='song_part_assignment_create'),
    path("songs/<int:song_id>/part-assignments/<int:pk>/update/",
         views.SongPartAssignmentUpdateView.as_view(), name="song_part_assignment_update"),

    path("part-assignments/", views.PartAssignmentListView.as_view(), name="part_assignment_list"),

    path("gigs/", views.GigListView.as_view(), name="gig_list"),
    path("gigs/create/", views.GigCreateView.as_view(), name="gig_create"),
    path("gigs/<int:pk>/", views.GigDetailView.as_view(), name="gig_detail"),
    path("gigs/<int:pk>/update/", views.GigUpdateView.as_view(), name="gig_update"),
    path("gigs/<int:gig_id>/availability/update", views.GigAvailabilityUpdateView.as_view(), name="gig_availability_update"),
    path("gigs/<int:pk>/gig-part-assignments/", views.GigPartAssignmentsDetailView.as_view(), name="gig_part_assignments_detail"),
    path("gigs/<int:pk>/gig-part-assignments/create", views.GigPartAssignmentOverrideCreateView.as_view(), name="gig_part_assignment_override_create"),
    path("gigs/<int:pk>/gig-part-assignments/print", views.GigPartAssignmentPrintView.as_view(), name="gig_part_assignment_print"),
    path("gigs/<int:gig_id>/setlist/update", views.GigSetlistUpdateView.as_view(), name="gig_setlist_update"),
    path("gigs/<int:gig_id>/setlist/add-song/<int:song_id>", views.GigSetlistAddSongView.as_view(), name='gig_setlist_add_song'),

    path("instruments/", views.InstrumentListView.as_view(), name="instrument_list"),

    path("health/", views.health_check)
]
