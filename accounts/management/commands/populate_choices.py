from django.core.management.base import BaseCommand
from accounts.models import ManufacturingSector, ManufacturingTech, MaterialCapability

class Command(BaseCommand):
    help = 'Populate the ManufacturingSector, ManufacturingTech and MaterialCapability lookup tables'

    def handle(self, *args, **kwargs):
        # Populate ManufacturingSector
        sector_choices = ManufacturingSector.TECHNOLOGY_CHOICES
        for choice in sector_choices:
            technology, created = ManufacturingSector.objects.get_or_create(
                technology=choice[0],
                defaults={'description': choice[1]}
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Successfully created sector: {choice[1]}'))
            else:
                self.stdout.write(self.style.WARNING(f'Sector already exists: {choice[1]}'))

        # Populate ManufacturingTech.
        # NOTE: `technology_type` is the lookup key (must stay one of the
        # TECH_CHOICES keys, e.g. 'milling') — it must NOT also appear in
        # `defaults`, since get_or_create() merges defaults into the create
        # kwargs and would overwrite the key with the human-readable label.
        # (A previous version of this command passed the label via
        # `defaults={'technology_type': choice[1]}`, which silently stored
        # labels instead of keys in every newly-created row.)
        tech_choices = ManufacturingTech.TECH_CHOICES
        for choice in tech_choices:
            technology_type, created = ManufacturingTech.objects.get_or_create(
                technology_type=choice[0],
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Successfully created tech: {choice[1]}'))
            else:
                self.stdout.write(self.style.WARNING(f'Tech already exists: {choice[1]}'))

        # Populate MaterialCapability
        material_choices = MaterialCapability.MATERIAL_CHOICES
        for choice in material_choices:
            material_type, created = MaterialCapability.objects.get_or_create(
                material_type=choice[0],
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Successfully created material: {choice[1]}'))
            else:
                self.stdout.write(self.style.WARNING(f'Material already exists: {choice[1]}'))

        self.stdout.write(self.style.SUCCESS('Successfully populated all choices'))
