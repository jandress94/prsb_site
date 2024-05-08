from django.db import models


class Song(models.Model):
    name = models.CharField(max_length=256)
    author = models.CharField(max_length=256, blank=True)
    duration = models.DurationField()
    is_active = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class SongPart(models.Model):
    song = models.ForeignKey(Song, on_delete=models.CASCADE)
    name = models.CharField(max_length=256)

    def __str__(self):
        return f'{self.song.name}: {self.name}'
