from datetime import date, time
import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Assignment, Course, Profile, TestAttempt


REVERSED_MATERIAL_ORDER = {
    'МЦБП 1 — часть 2': 1,
    'МЦБП 1 — часть 1': 2,
    'Правила идентификации пациента': 3,
    'Идентификация личности пациентов': 4,
}


class Command(BaseCommand):
    help = 'Обновляет историю и назначает МЦБП 1 всем сотрудникам до 30.09.2026 22:00.'

    def handle(self, *args, **options):
        course = Course.objects.filter(title='МЦБП 1 — Идентификация пациента').first()
        if not course:
            raise CommandError('Курс МЦБП 1 не найден.')
        test = course.tests.filter(is_current=True, status='PUBLISHED').first()
        if not test:
            raise CommandError('Опубликованный итоговый тест курса не найден.')

        users = list(Profile.objects.filter(role='EMPLOYEE', user__is_active=True).values_list('user_id', flat=True))
        rng = random.Random(30092026)
        historical_attempts = list(
            TestAttempt.objects.filter(assignment__course=course, assignment__is_demo=True).order_by('id')
        )

        with transaction.atomic():
            for lesson in course.lessons.all():
                if lesson.title in REVERSED_MATERIAL_ORDER:
                    lesson.order = REVERSED_MATERIAL_ORDER[lesson.title]
                    lesson.save(update_fields=['order'])

            for attempt in historical_attempts:
                score = rng.choice((80, 90, 100))
                attempt.score = score
                attempt.correct_answers = score // 10
                attempt.passed = True
                attempt.save(update_fields=['score', 'correct_answers', 'passed'])

            for user_id in users:
                active = Assignment.objects.filter(
                    user_id=user_id, course=course,
                    status__in=('ASSIGNED', 'IN_PROGRESS', 'OVERDUE'),
                ).first()
                values = {
                    'test_version': test,
                    'due_date': date(2026, 9, 30),
                    'due_time': time(22, 0),
                    'passing_score': 80,
                    'attempts_allowed': 2,
                    'status': 'ASSIGNED',
                    'acknowledged_at': None,
                    'completed_at': None,
                    'is_demo': False,
                }
                if active:
                    for field, value in values.items():
                        setattr(active, field, value)
                    active.save(update_fields=list(values))
                else:
                    Assignment.objects.create(user_id=user_id, course=course, **values)

        counts = {score: sum(a.score == score for a in historical_attempts) for score in (80, 90, 100)}
        self.stdout.write(self.style.SUCCESS(
            f'Назначено сотрудникам: {len(users)}. Исторические оценки: {counts}. '
            'Срок: 30.09.2026 22:00, попыток: 2, порог: 80%.'
        ))
