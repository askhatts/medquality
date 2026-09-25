from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Assignment, Course, Profile, TestAttempt


class Command(BaseCommand):
    help = 'Создаёт идемпотентную историю обучения только для демонстрационных пользователей.'

    def handle(self, *args, **options):
        users = [p.user for p in Profile.objects.select_related('user').filter(user__is_active=True).exclude(user__username='sysadmin') if p.user.username == 'methodist' or p.user.username == 'head' or p.user.username.startswith('employee')]
        courses = list(Course.objects.filter(active=True).prefetch_related('tests'))
        if not users or not courses:
            return self.stdout.write(self.style.WARNING('Нет демонстрационных пользователей или курсов.'))
        now = timezone.now()
        statuses = ('COMPLETED', 'FAILED', 'IN_PROGRESS', 'ASSIGNED', 'OVERDUE')
        created = 0
        for user_index, user in enumerate(users):
            for cycle in range(4):
                course = courses[(user_index + cycle) % len(courses)]
                test = course.tests.filter(is_current=True, status='PUBLISHED').first() or course.tests.filter(is_current=True).first()
                status = statuses[(user_index + cycle) % len(statuses)]
                assigned_at = now - timedelta(days=35 + cycle * 73 + user_index * 4)
                due_date = (assigned_at + timedelta(days=21)).date()
                key = f'report-demo:{user.username}:{course.id}:{cycle}'
                assignment, was_created = Assignment.objects.get_or_create(
                    demo_key=key,
                    defaults={'user': user, 'course': course, 'test_version': test, 'due_date': due_date, 'passing_score': 80, 'attempts_allowed': 3, 'status': status, 'is_demo': True},
                )
                if not was_created:
                    continue
                created += 1
                Assignment.objects.filter(pk=assignment.pk).update(assigned_at=assigned_at)
                assignment.refresh_from_db()
                if status == 'ASSIGNED':
                    continue
                acknowledged_at = assigned_at + timedelta(days=2)
                Assignment.objects.filter(pk=assignment.pk).update(acknowledged_at=acknowledged_at)
                if status in ('IN_PROGRESS', 'OVERDUE'):
                    scores = [55 + (user_index * 3 + cycle) % 20]
                elif status == 'FAILED':
                    scores = [45 + user_index % 10, 58 + cycle, 68 + user_index % 8]
                else:
                    scores = [62 + user_index % 10, 82 + (user_index * 3 + cycle) % 18]
                for number, score in enumerate(scores, 1):
                    attempt = TestAttempt.objects.create(assignment=assignment, number=number, correct_answers=max(1, round(score / 20)), score=min(score, 100), passed=score >= 80, answers_snapshot=[{'demo': True, 'score': score}])
                    TestAttempt.objects.filter(pk=attempt.pk).update(completed_at=acknowledged_at + timedelta(days=number * 2))
                if status == 'COMPLETED':
                    Assignment.objects.filter(pk=assignment.pk).update(completed_at=acknowledged_at + timedelta(days=len(scores) * 2))
        self.stdout.write(self.style.SUCCESS(f'Создано демонстрационных циклов: {created}.'))
