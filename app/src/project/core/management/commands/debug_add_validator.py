from django.core.management import BaseCommand

from project.core.models import Validator


class Command(BaseCommand):
    """For local development, whitelist a hotkey without registering it on-chain."""

    def add_arguments(self, parser):
        parser.add_argument("validator_public_key", type=str, help="hotkey SS58 address")
        parser.add_argument("--netuid", type=int, required=True, help="subnet uid the validator belongs to")

    def handle(self, *args, **options):
        Validator.objects.create(
            public_key=options["validator_public_key"],
            netuid=options["netuid"],
            active=True,
            debug=True,
        )
