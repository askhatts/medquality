from django.conf import settings

from .assignment_queries import pending_course_assignments
from .models import PasswordResetRequest

def portal(request):
    if not request.user.is_authenticated:
        return {
            'notification_count': 0,
            'password_request_count': 0,
            'session_idle_timeout_ms': settings.SESSION_COOKIE_AGE * 1000,
        }
    user_profile = getattr(request.user, 'profile', None)
    password_request_count = 0
    if user_profile and user_profile.role == 'ADMIN':
        password_request_count = PasswordResetRequest.objects.filter(status='PENDING').count()
    return {
        'notification_count': pending_course_assignments(request.user).count(),
        'password_request_count': password_request_count,
        'session_idle_timeout_ms': settings.SESSION_COOKIE_AGE * 1000,
    }
