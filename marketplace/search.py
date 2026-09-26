"""PostgreSQL full-text search over RFQs and manufacturers.

Vectors are built at query time rather than stored, so there's nothing to
keep in sync when parts, capabilities or certifications change. Each
search also ORs in a plain substring match, so partial words ("alumin")
and RFQ numbers still find results the stemmed full-text match would miss;
those rank below real full-text hits.
"""
from django.contrib.postgres.aggregates import StringAgg
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank, SearchVector
from django.db.models import Case, CharField, F, OuterRef, Q, Subquery, TextField, Value, When
from django.db.models.functions import Coalesce, Concat

from accounts.models import Certification, Machine, ManufacturingTech, MaterialCapability
from .models import RequirementPart

SEARCH_CONFIG = 'english'
# Postgres wraps matches in these; the `search_highlight` template filter
# escapes the snippet first and only then turns them into <mark> tags.
HIGHLIGHT_START = '\x02'
HIGHLIGHT_STOP = '\x03'


def _search_query(text):
    return SearchQuery(text, search_type='websearch', config=SEARCH_CONFIG)


def _joined(queryset, group_by, expression):
    """A correlated subquery that joins `expression` over the related rows
    into one space-separated string ('' when there are none)."""
    subquery = (
        queryset.order_by().values(group_by)
        .annotate(joined=StringAgg(expression, delimiter=' '))
        .values('joined')
    )
    return Coalesce(Subquery(subquery, output_field=TextField()), Value(''), output_field=TextField())


def _choice_label(field, choices):
    """SQL CASE mapping a stored choice code to its human label, so people
    can search "Sheet metal fabrication" rather than "sheet_metal"."""
    return Case(*[When(**{field: code}, then=Value(label)) for code, label in choices], default=F(field), output_field=CharField())


def search_requirements(queryset, text):
    """Ranks and filters RFQs by `text`. Adds `rank` and `snippet`."""
    query = _search_query(text)
    queryset = queryset.annotate(
        parts_text=_joined(
            RequirementPart.objects.filter(requirement=OuterRef('pk')), 'requirement',
            Concat('part_name', Value(' '), Coalesce('Part_desc', Value('')), Value(' '), 'technology', Value(' '), 'Material'),
        ),
    ).annotate(
        search=(
            SearchVector('title', weight='A', config=SEARCH_CONFIG)
            + SearchVector('parts_text', weight='B', config=SEARCH_CONFIG)
            + SearchVector(Coalesce('industry', Value('')), 'rfq_desc', weight='C', config=SEARCH_CONFIG)
        ),
    )
    queryset = queryset.filter(
        Q(search=query) | Q(title__icontains=text) | Q(rfq_desc__icontains=text) | Q(parts_text__icontains=text)
    )
    return queryset.annotate(
        rank=SearchRank(F('search'), query),
        snippet=SearchHeadline(
            Concat('rfq_desc', Value(' · Parts: '), 'parts_text', output_field=TextField()), query,
            config=SEARCH_CONFIG, start_sel=HIGHLIGHT_START, stop_sel=HIGHLIGHT_STOP, max_words=30, min_words=12,
        ),
    )


def search_suppliers(queryset, text):
    """Ranks and filters ManufacturerProfiles by `text`. Adds `rank` and `snippet`."""
    query = _search_query(text)
    queryset = queryset.annotate(
        capabilities_text=_joined(
            ManufacturingTech.objects.filter(manufacturers=OuterRef('pk')), 'manufacturers',
            _choice_label('technology_type', ManufacturingTech.TECH_CHOICES),
        ),
        materials_text=_joined(
            MaterialCapability.objects.filter(manufacturers=OuterRef('pk')), 'manufacturers',
            _choice_label('material_type', MaterialCapability.MATERIAL_CHOICES),
        ),
        certifications_text=_joined(Certification.objects.filter(manufacturer=OuterRef('pk')), 'manufacturer', 'name'),
        machines_text=_joined(
            Machine.objects.filter(manufacturer=OuterRef('pk')), 'manufacturer',
            Concat('machine_type', Value(' '), 'make_model'),
        ),
    ).annotate(
        search=(
            SearchVector(Coalesce('companyname', Value('')), weight='A', config=SEARCH_CONFIG)
            + SearchVector('capabilities_text', 'materials_text', 'certifications_text', weight='B', config=SEARCH_CONFIG)
            + SearchVector('about', 'machines_text', 'city', 'state', weight='C', config=SEARCH_CONFIG)
        ),
    )
    queryset = queryset.filter(
        Q(search=query) | Q(companyname__icontains=text) | Q(city__icontains=text)
        | Q(capabilities_text__icontains=text) | Q(materials_text__icontains=text) | Q(certifications_text__icontains=text)
    )
    return queryset.annotate(
        rank=SearchRank(F('search'), query),
        snippet=SearchHeadline(
            Concat(
                'capabilities_text', Value(' · '), 'materials_text', Value(' · '), 'certifications_text',
                Value(' · '), 'about', output_field=TextField(),
            ),
            query, config=SEARCH_CONFIG, start_sel=HIGHLIGHT_START, stop_sel=HIGHLIGHT_STOP, max_words=25, min_words=10,
        ),
    )
