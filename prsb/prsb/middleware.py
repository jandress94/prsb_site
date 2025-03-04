#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# middleware.py
# Created: 3/3/25

from django.contrib.auth.decorators import login_required
from django.utils.deprecation import MiddlewareMixin

class LoginRequiredMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.user.is_authenticated and not request.path.startswith("/accounts/login/"):
            return login_required(view_func)(request, *view_args, **view_kwargs)
