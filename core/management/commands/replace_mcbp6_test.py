"""Publish the corrected ten-question version of the MCBP 6 test.

The original course was published with nine questions.  That made the
otherwise natural result of seven correct answers display as 78%, so an 80%
passing threshold could not be reached in that case.  This command creates a
new immutable version and gives it only to the latest incomplete assignment of
each employee.  Completed cycles and every recorded attempt remain untouched.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Assignment, Course, Question, Test


COURSE_TITLE = 'МЦБП 6 — Профилактика падений'
TEST_TITLE = 'Итоговый тест: МЦБП 6 — версия 2'

# Each tuple is: question, answer variants, index of the one correct answer,
# and a short explanation shown after submission.
QUESTIONS = [
    (
        'Когда проводится первичная оценка риска падения у пациента?',
        [
            'Только перед выпиской из отделения',
            'При поступлении пациента в отделение',
            'Только после зарегистрированного падения',
            'Один раз в конце лечения',
        ],
        1,
        'Первичная оценка позволяет сразу определить потребность в профилактических мерах.',
    ),
    (
        'Какая шкала применяется для оценки риска падения у взрослых пациентов в курсе?',
        [
            'Шкала Брейдена',
            'Шкала Глазго',
            'Шкала Морзе',
            'Шкала NEWS',
        ],
        2,
        'Для оценки риска падений у взрослых используется шкала Морзе.',
    ),
    (
        'Какой результат по шкале Морзе относят к высокому риску падения?',
        [
            '0–24 балла',
            '51 балл и выше',
            '25–50 баллов',
            'Только ровно 100 баллов',
        ],
        1,
        'Высокий риск по шкале Морзе начинается с 51 балла.',
    ),
    (
        'В каком случае оценку риска падения необходимо повторить до очередной плановой оценки?',
        [
            'Только по просьбе родственника',
            'Только при выписке пациента',
            'Только после смены дежурной медицинской сестры',
            'После изменения состояния пациента, падения или процедуры, способной повлиять на риск',
        ],
        3,
        'При изменении состояния риск может измениться, поэтому оценку актуализируют без ожидания.',
    ),
    (
        'Какое действие является частью профилактики падения у пациента высокого риска?',
        [
            'Обеспечить доступность кнопки вызова и помощь при перемещении по необходимости',
            'Убрать кнопку вызова, чтобы пациент не вставал',
            'Разрешить самостоятельное передвижение без оценки состояния',
            'Разместить пациента как можно дальше от поста медицинской сестры',
        ],
        0,
        'Пациент должен иметь возможность вызвать помощь, а персонал — сопровождать его при необходимости.',
    ),
    (
        'Что следует проверить у кровати пациента, чтобы снизить риск падения?',
        [
            'Что личные вещи находятся вне досягаемости пациента',
            'Что кровать установлена на максимальную высоту',
            'Что тормоза кровати зафиксированы, проход свободен, а необходимые предметы доступны',
            'Что ночное освещение полностью выключено',
        ],
        2,
        'Безопасная среда у кровати включает зафиксированную кровать, свободный проход и доступность нужных предметов.',
    ),
    (
        'Какова первоочередная последовательность действий персонала после падения пациента?',
        [
            'Обеспечить безопасность пациента, оценить его состояние, сообщить ответственному сотруднику и оформить событие по локальному порядку',
            'Сразу попросить пациента самостоятельно вернуться в кровать',
            'Сначала оформить отчёт, а затем подойти к пациенту',
            'Не фиксировать событие, если внешней травмы не видно',
        ],
        0,
        'Сначала важны безопасность и оценка состояния пациента; документирование выполняют после первичных действий.',
    ),
    (
        'Для чего оформляется сообщение об инциденте, связанном с падением?',
        [
            'Чтобы определить виновного сотрудника',
            'Только для статистики в конце года',
            'Только при тяжёлой травме',
            'Чтобы зафиксировать факты, проанализировать причины и предупредить повторные случаи',
        ],
        3,
        'Сообщение об инциденте — инструмент улучшения процессов и профилактики повторения.',
    ),
    (
        'Когда амбулаторного пациента следует отнести к группе риска падения по скринингу?',
        [
            'Только если он старше 80 лет',
            'Если хотя бы на один вопрос скрининга получен ответ «да»',
            'Только при наличии падения в стационаре',
            'Только если на все вопросы получен ответ «да»',
        ],
        1,
        'Для направления на профилактические меры достаточно положительного ответа хотя бы на один вопрос скрининга.',
    ),
    (
        'Какую информацию важно сообщить пациенту с риском падения и его сопровождающему?',
        [
            'Не сообщать о риске, чтобы не вызвать тревогу',
            'Объяснить, что помощь потребуется только после падения',
            'Попросить использовать кнопку вызова и дождаться помощи при вставании или перемещении',
            'Разрешить вставать самостоятельно в любое время',
        ],
        2,
        'Пациент и сопровождающий должны знать, как безопасно запросить помощь.',
    ),
]


class Command(BaseCommand):
    help = 'Публикует исправленную версию из 10 вопросов для МЦБП 6 и переназначает её незавершившим сотрудникам.'

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            course = Course.objects.get(title=COURSE_TITLE)
        except Course.DoesNotExist as exc:
            raise CommandError(f'Курс «{COURSE_TITLE}» не найден.') from exc

        test = course.tests.filter(title=TEST_TITLE).first()
        if test:
            if test.questions.count() != len(QUESTIONS):
                raise CommandError('Исправленная версия уже существует, но содержит неверное число вопросов.')
            created_test = False
        else:
            next_version = (course.tests.order_by('-version').values_list('version', flat=True).first() or 0) + 1
            course.tests.filter(is_current=True).update(is_current=False)
            test = Test.objects.create(
                course=course,
                title=TEST_TITLE,
                instructions=(
                    'Выберите один правильный ответ на каждый вопрос. '
                    'Порядок вариантов меняется при открытии теста.'
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
                for order, (text, options, correct_index, explanation) in enumerate(QUESTIONS, 1)
            ])
            created_test = True

        # A new version is a new learning cycle.  Do not overwrite old attempts:
        # retain them on the previous assignment and make a clean assignment for
        # the corrected test.  Selecting only the latest cycle prevents a second
        # historical assignment for the same user from being moved on reruns.
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

        verb = 'создана' if created_test else 'уже существует'
        self.stdout.write(self.style.SUCCESS(
            f'Исправленная версия {verb}: {test.version}; вопросов: {test.questions.count()}; '
            f'переназначено незавершившим сотрудникам: {reassigned}.'
        ))
