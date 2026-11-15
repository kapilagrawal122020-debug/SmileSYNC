from django.core.management.base import BaseCommand

from masterdata import services
from masterdata.models import ListDefinition, ListValue


class Command(BaseCommand):
    help = "Load the master lists from masterdata/seed/master_lists.json. Adds what is missing and never overwrites an edited label."

    def handle(self, *args, **options):
        result = services.load_seed(ListDefinition, ListValue)
        self.stdout.write(self.style.SUCCESS(
            f"Lists created {result['lists_created']}, values created {result['values_created']}. Existing lists and values were left as they are."
        ))
