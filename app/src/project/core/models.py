from django.db import models


class Validator(models.Model):
    public_key = models.TextField()
    netuid = models.IntegerField(default=0)
    active = models.BooleanField()
    debug = models.BooleanField(default=False)

    class Meta:
        unique_together = [("public_key", "netuid")]

    def __str__(self):
        return f"hotkey: {self.public_key} netuid: {self.netuid}"
