from datetime import time

from django.db.models import Count, F, OuterRef, Q, Subquery
from django.utils import timezone

from .models import Assignment


def latest_course_assignments(user):
    """The newest cycle wins even when cancelled, finished or missing a test."""
    latest = Assignment.objects.filter(
        user_id=OuterRef('user_id'), course_id=OuterRef('course_id'),
    ).order_by('-assigned_at', '-id').values('pk')[:1]
    return Assignment.objects.filter(
        user=user,
        user__is_active=True,
        course__active=True,
        pk=Subquery(latest),
    )


def pending_course_assignments(user):
    """Only tests that can actually be taken; shared by cards and the bell."""
    now = timezone.localtime()
    today, current_time = now.date(), now.time()
    today_time = Q(due_time__gte=current_time)
    if current_time <= time(23, 59, 59):
        today_time |= Q(due_time__isnull=True)
    deadline_open = Q(due_date__gt=today) | (Q(due_date=today) & today_time)
    return latest_course_assignments(user).filter(
        deadline_open,
        status__in=('ASSIGNED', 'IN_PROGRESS', 'OVERDUE'),
        test_version__status='PUBLISHED',
    ).annotate(used_attempts=Count('attempts')).filter(used_attempts__lt=F('attempts_allowed'))
