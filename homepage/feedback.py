"""Portal feedback: the public rating and best reviews shown before login,
and the staff summary of what users find useful and want improved."""
from collections import Counter

from django.core.cache import cache
from django.db.models import Avg, Count

from accounts.models import ConsumerProfile, ManufacturerProfile
from .models import PortalFeedback, PORTAL_FEATURES, PUBLIC_FEEDBACK_CACHE_KEY

PUBLIC_REVIEW_LIMIT = 3
PUBLIC_REVIEW_MIN_RATING = 4


def _company_name(user):
    if user.role == 'manufacturer':
        profile = ManufacturerProfile.objects.filter(user=user).first()
        return profile.companyname if profile else ''
    profile = ConsumerProfile.objects.filter(user=user).first()
    return profile.Name if profile else ''


def _display_name(user):
    first = (user.first_name or '').strip()
    last = (user.last_name or '').strip()
    if first:
        return f"{first} {last[:1]}." if last else first
    return "A ManufactureHub user"


def best_reviews_queryset():
    """Reviews that may appear on the public site: the author opted in, staff
    haven't hidden it, it has text and it rates 4 or 5. Staff-featured ones
    come first, then the highest rated, then the most recent."""
    return (
        PortalFeedback.objects.filter(allow_public=True, is_hidden=False, rating__gte=PUBLIC_REVIEW_MIN_RATING)
        .exclude(comment='')
        .select_related('user')
        .order_by('-is_featured', '-rating', '-updated_at')
    )


def public_summary():
    """Overall rating plus the best few reviews, cached until feedback changes."""
    summary = cache.get(PUBLIC_FEEDBACK_CACHE_KEY)
    if summary is not None:
        return summary
    stats = PortalFeedback.objects.aggregate(avg=Avg('rating'), count=Count('pk'))
    reviews = [
        {
            'comment': feedback.comment,
            'rating': feedback.rating,
            'name': _display_name(feedback.user),
            'company': _company_name(feedback.user),
            'role': 'Manufacturer' if feedback.role == 'manufacturer' else 'Buyer',
        }
        for feedback in best_reviews_queryset()[:PUBLIC_REVIEW_LIMIT]
    ]
    summary = {'avg': stats['avg'], 'count': stats['count'], 'reviews': reviews}
    cache.set(PUBLIC_FEEDBACK_CACHE_KEY, summary, 60 * 60)
    return summary


def staff_summary():
    """Everything the staff feedback page shows."""
    feedback = list(PortalFeedback.objects.select_related('user').order_by('-updated_at'))
    total = len(feedback)
    useful = Counter(key for f in feedback for key in f.useful_features)
    improve = Counter(key for f in feedback for key in f.improvement_areas)
    features = sorted(
        (
            {'label': label, 'useful': useful[key], 'improve': improve[key],
             'useful_percent': useful[key] * 100 // total if total else 0,
             'improve_percent': improve[key] * 100 // total if total else 0}
            for key, label in PORTAL_FEATURES
        ),
        key=lambda row: (-row['improve'], -row['useful']),
    )
    by_role = {
        row['role']: row for row in
        PortalFeedback.objects.values('role').annotate(avg=Avg('rating'), count=Count('pk')).order_by()
    }
    distribution = Counter(f.rating for f in feedback)
    return {
        'total': total,
        'avg': sum(f.rating for f in feedback) / total if total else None,
        'buyers': by_role.get('consumer'),
        'manufacturers': by_role.get('manufacturer'),
        'stars': [
            {'star': star, 'count': distribution[star], 'percent': distribution[star] * 100 // total if total else 0}
            for star in range(5, 0, -1)
        ],
        'features': features,
        'improvement_notes': [f for f in feedback if f.improvement_note][:20],
        'reviews': [f for f in feedback if f.comment],
    }
