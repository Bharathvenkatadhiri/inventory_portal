from django.core.management.base import BaseCommand
from accounts.models import ManufacturingSector, ManufacturingTech

class Command(BaseCommand):
    help = 'Populate the ManufacturingSector and ManufacturingTech models with predefined choices'

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

        # Populate ManufacturingTech
        tech_choices = ManufacturingTech.TECH_CHOICES
        for choice in tech_choices:
            technology_type, created = ManufacturingTech.objects.get_or_create(
                technology_type=choice[0],
                defaults={'technology_type': choice[1]}
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Successfully created tech: {choice[1]}'))
            else:
                self.stdout.write(self.style.WARNING(f'Tech already exists: {choice[1]}'))

        self.stdout.write(self.style.SUCCESS('Successfully populated all choices'))
