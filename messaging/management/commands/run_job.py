from django.core.management.base import BaseCommand

from messaging.jobs import JOBS


class Command(BaseCommand):
    help = "Run one scheduled job (see messaging/jobs.py for the list and suggested times)."

    def add_arguments(self, parser):
        parser.add_argument("job", choices=sorted(JOBS))

    def handle(self, *args, job, **options):
        JOBS[job]()
        self.stdout.write(self.style.SUCCESS(f"Job '{job}' finished."))
