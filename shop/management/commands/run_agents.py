import time
from django.core.management.base import BaseCommand
from shop.automation import sweep, run_jobs


class Command(BaseCommand):
    help = "Run durable commerce automation. Use --watch in a separate terminal or --once from a scheduler."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=5)
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        try:
            while True:
                sweep()
                count = run_jobs(limit=min(max(options["limit"], 1), 500))
                self.stdout.write(f"Processed {count} jobs.")
                if not options["watch"]:
                    break
                time.sleep(min(max(options["interval"], 1), 30))
        except KeyboardInterrupt:
            self.stdout.write("Automation worker stopped.")
