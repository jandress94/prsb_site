from django.http import HttpResponse
from django.views import generic

from .models import Song


def index(request):
    return HttpResponse("Hello, world. You're at the band index")


class SongListView(generic.ListView):
    model = Song


class SongDetailView(generic.DetailView):
    model = Song
