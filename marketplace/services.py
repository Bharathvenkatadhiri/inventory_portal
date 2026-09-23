"""Shared business logic for the manufacturer dashboard.

Kept separate from models.py because match-percent computation needs data
from both `accounts` (ManufacturerProfile capabilities/materials) and
`marketplace` (RequirementPart) — putting it as a model method on either
side would need that side to import the other, risking circularity.
"""
from django.utils import timezone

from .models import Requirement

# RequirementPart.TECHNOLOGY_TYPES (broad RFQ process categories, e.g.
# "Milling") and ManufacturingTech.TECH_CHOICES (granular machine
# capabilities, e.g. "5_axis_milling") are different vocabularies — this
# maps an RFQ's process to the set of capability keys that satisfy it.
PROCESS_CAPABILITY_MAP = {
    'Milling': {'milling', '5_axis_milling', 'form_milling', 'full_range_milling', 'hsc_milling', 'engraver_milling'},
    'Turning': {'turning', 'full_range_turning'},
    'Full-range turning': {'full_range_turning'},
    'Anodizing': {'anodizing', 'anodizing_partner'},
}

# RequirementPart.MATERIAL_TYPES labels -> MaterialCapability.material_type keys.
MATERIAL_KEY_MAP = {
    'Structural steel': 'structural_steel',
    'Stainless steel': 'stainless_steel',
    'Aluminium': 'aluminium',
    'Case hardening': 'case_hardening',
}

PROCESS_WEIGHT = 60
MATERIAL_WEIGHT = 40


def _part_score(part, capability_keys, material_keys):
    score = 0
    if PROCESS_CAPABILITY_MAP.get(part.technology, set()) & capability_keys:
        score += PROCESS_WEIGHT
    if MATERIAL_KEY_MAP.get(part.Material) in material_keys:
        score += MATERIAL_WEIGHT
    return score


def compute_match_percent(requirement, manufacturer):
    """Real match score (0-100) for one requirement against one
    manufacturer's tagged capabilities/materials — not a placeholder.
    Returns None (not 0) when the manufacturer hasn't configured either
    yet, so an incomplete profile doesn't look like a bad match — it looks
    like "finish your profile"."""
    capability_keys = set(manufacturer.capabilities.values_list('technology_type', flat=True))
    material_keys = set(manufacturer.materials.values_list('material_type', flat=True))
    if not capability_keys and not material_keys:
        return None

    parts = list(requirement.requirement_parts.all())
    if not parts:
        return None

    total = sum(_part_score(part, capability_keys, material_keys) for part in parts)
    return round(total / len(parts))


def bulk_match_percent(requirements, manufacturer):
    """Same computation, batched: fetches the manufacturer's
    capability/material key sets once, then loops in Python over an
    already-small (paginated) list of requirements — avoids N+1 queries in
    list templates."""
    capability_keys = set(manufacturer.capabilities.values_list('technology_type', flat=True))
    material_keys = set(manufacturer.materials.values_list('material_type', flat=True))
    results = {}
    for requirement in requirements:
        if not capability_keys and not material_keys:
            results[requirement.pk] = None
            continue
        parts = list(requirement.requirement_parts.all())
        if not parts:
            results[requirement.pk] = None
            continue
        total = sum(_part_score(part, capability_keys, material_keys) for part in parts)
        results[requirement.pk] = round(total / len(parts))
    return results


def open_requirements_for(manufacturer):
    """The set of RFQs open for a manufacturer to quote on: not expired,
    not deleted, and not already awarded to someone else. Extracted from
    RequirementListView.get_queryset() so the dashboard stats and the RFQ
    inbox can't drift apart the way HomeView's old count did."""
    return Requirement.objects.filter(
        end_date__gte=timezone.now(), is_deleted=False
    ).exclude(quote__is_selected=True).distinct()
