from .models import Assignment

def portal(request):
    if not request.user.is_authenticated:
        return {'notification_count': 0}
    return {'notification_count': Assignment.objects.filter(user=request.user, status__in=['ASSIGNED', 'IN_PROGRESS']).count()}
