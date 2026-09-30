from .models import Assignment, PasswordResetRequest

def portal(request):
    if not request.user.is_authenticated:
        return {'notification_count': 0, 'password_request_count': 0}
    user_profile = getattr(request.user, 'profile', None)
    password_request_count = 0
    if user_profile and user_profile.role == 'ADMIN':
        password_request_count = PasswordResetRequest.objects.filter(status='PENDING').count()
    return {
        'notification_count': Assignment.objects.filter(
            user=request.user, status__in=['ASSIGNED', 'IN_PROGRESS']
        ).count(),
        'password_request_count': password_request_count,
    }
