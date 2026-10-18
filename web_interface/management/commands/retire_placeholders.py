from django.core.management.base import BaseCommand
from scripts.open_data import looks_like_placeholder
from web_interface.models import Destination, PlaceToEat

class Command(BaseCommand):
    help = "Reject placeholder-named rows that have no real submitter."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--yes', action='store_true')
        parser.add_argument('--undo', action='store_true')

    def handle(self, *args, **options):
        from_status, to_status = ('rejected', 'approved') if options['undo'] else ('approved', 'rejected')
        targets = []
        for kind, Model in (('destination', Destination), ('restaurant', PlaceToEat)):
            qs = Model.objects.filter(status=from_status, submitted_by__isnull=True).select_related('location')
            for place in qs:
                if looks_like_placeholder(place.name):
                    targets.append((kind, place))
        if not targets:
            self.stdout.write('Nothing to do -- no matching rows found.')
            return
        self.stdout.write(f'{len(targets)} row(s) -> {to_status!r}:')
        for kind, place in targets:
            self.stdout.write(f'  [{kind[:4]}] {place.location.name:16} {place.name}')
        if options['dry_run']:
            self.stdout.write('DRY RUN -- nothing changed.')
            return
        if not options['yes']:
            if input(f'Set {len(targets)} row(s) to {to_status!r}? [y/N] ').strip().lower() not in ('y', 'yes'):
                self.stdout.write('Cancelled.')
                return
        for kind, place in targets:
            place.status = to_status
            place.save(update_fields=['status'])
        self.stdout.write(self.style.SUCCESS(f'Done. {len(targets)} row(s) set to {to_status!r}.'))
