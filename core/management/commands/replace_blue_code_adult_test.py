"""Publish an adult-only Blue Code test without changing completed history."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Assignment, Course, Question, Test
from core.training_content import BLUE_CODE_ADULT_QUESTIONS


COURSE_TITLE = 'Синий код: базовая реанимация и действия команды'
TEST_TITLE = 'Итоговый тест: Синий код — базовая реанимация взрослых'


class Command(BaseCommand):
    help = 'Создаёт взрослую версию теста Синего кода и переводит на неё незавершённые назначения.'

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            course = Course.objects.get(title=COURSE_TITLE)
        except Course.DoesNotExist as exc:
            raise CommandError(f'Курс «{COURSE_TITLE}» не найден.') from exc

        test = course.tests.filter(title=TEST_TITLE).first()
        if test:
            actual_texts = list(test.questions.order_by('order').values_list('text', flat=True))
            expected_texts = [item[0] for item in BLUE_CODE_ADULT_QUESTIONS]
            if actual_texts != expected_texts:
                raise CommandError('Взрослая версия уже существует, но её вопросы отличаются от ожидаемых.')
            created_test = False
            course.tests.exclude(pk=test.pk).filter(is_current=True).update(is_current=False)
            if not test.is_current:
                test.is_current = True
                test.save(update_fields=['is_current'])
        else:
            next_version = (course.tests.order_by('-version').values_list('version', flat=True).first() or 0) + 1
            course.tests.filter(is_current=True).update(is_current=False)
            test = Test.objects.create(
                course=course,
                title=TEST_TITLE,
                instructions=(
                    'Выберите один правильный ответ на каждый вопрос. '
                    'Все вопросы относятся к базовой сердечно-лёгочной реанимации взрослых.'
                ),
                version=next_version,
                status='PUBLISHED',
                is_current=True,
                time_limit_minutes=10,
            )
            Question.objects.bulk_create([
                Question(
                    test=test,
                    text=text,
                    question_type='SINGLE',
                    options=options,
                    correct_index=correct_index,
                    correct_indexes=[correct_index],
                    explanation=explanation,
                    points=1,
                    order=order,
                )
                for order, (text, options, correct_index, explanation)
                in enumerate(BLUE_CODE_ADULT_QUESTIONS, 1)
            ])
            created_test = True

        latest_by_user = {}
        for assignment in Assignment.objects.filter(course=course).order_by('user_id', '-assigned_at', '-id'):
            latest_by_user.setdefault(assignment.user_id, assignment)

        reassigned = 0
        for assignment in latest_by_user.values():
            if assignment.status in ('COMPLETED', 'CANCELLED', 'REASSIGNED'):
                continue
            if assignment.test_version_id == test.id:
                continue
            Assignment.objects.filter(pk=assignment.pk).update(
                status='REASSIGNED', current_attempt_started_at=None,
            )
            Assignment.objects.create(
                user=assignment.user,
                course=course,
                test_version=test,
                due_date=assignment.due_date,
                due_time=assignment.due_time,
                passing_score=assignment.passing_score,
                attempts_allowed=assignment.attempts_allowed,
                status='ASSIGNED',
                is_demo=assignment.is_demo,
            )
            reassigned += 1

        verb = 'создана' if created_test else 'уже существовала'
        self.stdout.write(self.style.SUCCESS(
            f'Взрослая версия теста {verb}: версия {test.version}; '
            f'вопросов: {test.questions.count()}; обновлено назначений: {reassigned}.'
        ))
