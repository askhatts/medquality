from django.db.models import OuterRef, Subquery

from .models import Assignment


def pending_course_assignments(user):
    '''The same current assignment cycles for course cards and navigation badges.'''
    latest = Assignment.objects.filter(
        user_id=OuterRef('user_id'), course_id=OuterRef('course_id'),
    ).order_by('-assigned_at', '-id').values('pk')[:1]
    return Assignment.objects.filter(
        user=user,
        course__active=True,
        pk=Subquery(latest),
        status__in=('ASSIGNED', 'IN_PROGRESS', 'OVERDUE', 'FAILED'),
        test_version__isnull=False,
    )
