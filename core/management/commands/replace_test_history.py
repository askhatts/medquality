"""Replace the explicitly designated test site's history with reproducible fixtures.

Only training history and questionnaire responses are replaced. Accounts,
materials, test versions, settings, and audit logs remain intact. Back up the
database before invoking --apply --replace-history on a hosted instance.
"""
import calendar
import json
import math
import random
import uuid
from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from core.models import Assignment, AuditLog, Course, Profile, SurveyCampaign, SurveyResponse, TestAttempt


def weekdays(year, month):
    return [date(year, month, day) for day in range(1, calendar.monthrange(year, month)[1] + 1)
            if date(year, month, day).weekday() < 5]


def timestamp(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def department_code(name):
    name = name.casefold()
    for fragments, code in [
        (('поликл', 'амбулат'), 'clinic'), (('химио',), 'chemo'),
        (('хирург',), 'surgery'), (('лучев',), 'radiotherapy'),
        (('радионукл', 'ядерн'), 'nuclear'), (('диагност',), 'diagnostics'),
    ]:
        if any(fragment in name for fragment in fragments):
            return code
    return 'other'


def category_code(name):
    name = name.casefold()
    if 'врач' in name:
        return 'doctor'
    if any(fragment in name for fragment in ('сестр', 'средн')):
        return 'nurse'
    if 'младш' in name:
        return 'junior'
    return 'other'


class Command(BaseCommand):
    help = 'Полная замена тестовой истории: два цикла (март/сентябрь), оценки 80/90/100 и 90–150 синтетических анкет.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=2026)
        parser.add_argument('--seed', type=int, default=20261119)
        parser.add_argument('--survey-count', type=int)
        parser.add_argument('--campaign', default='staff-wellbeing-2026-11')
        parser.add_argument('--apply', action='store_true', help='Выполнить изменения; без флага выводится только план.')
        parser.add_argument('--replace-history', action='store_true', help='Удалить предыдущие назначения, попытки и анкеты перед созданием набора.')

    def handle(self, *args, **options):
        year, seed = options['year'], options['seed']
        if not 2000 <= year <= 2099:
            raise CommandError('Год должен быть в диапазоне 2000–2099.')
        survey_rng = random.Random(seed + 99)
        survey_count = options['survey_count'] if options['survey_count'] is not None else survey_rng.randint(90, 150)
        if not 90 <= survey_count <= 150:
            raise CommandError('Количество анкет должно быть от 90 до 150.')
        campaign = SurveyCampaign.objects.filter(slug=options['campaign']).first()
        if not campaign:
            raise CommandError('Сначала подготовьте опрос командой prepare_ethics_and_survey.')
        people = list(Profile.objects.select_related('user', 'department', 'employee_type').filter(
            user__is_active=True, user__is_superuser=False,
        ).exclude(role='ADMIN').exclude(user__username='sysadmin').order_by('user_id'))
        if not people:
            raise CommandError('Нет активных сотрудников для тестового наполнения.')
        courses = list(Course.objects.filter(active=True).order_by('id').prefetch_related('tests__questions'))
        specifications = []
        test_questions = {}
        for course in courses:
            test = next((test for test in course.tests.all() if test.is_current and test.status == 'PUBLISHED'), None)
            questions = list(test.questions.all()) if test else []
            if not test or len(questions) != 10 or any(question.points != 1 for question in questions):
                raise CommandError(f'Курс «{course.title}»: требуется опубликованный тест из 10 равнозначных вопросов. История не изменена.')
            if any(len(question.options) < 2 or not set(question.correct_indexes or [question.correct_index]).issubset(range(len(question.options))) for question in questions):
                raise CommandError(f'Курс «{course.title}»: некорректные варианты ответов. История не изменена.')
            specifications.append((course, test))
            test_questions[test.pk] = questions
        if not specifications:
            raise CommandError('Нет активных курсов.')
        if len(courses) > min(len(weekdays(year, month)) - 1 for month in (3, 9)):
            raise CommandError('Слишком много курсов для разных будних дат в двух месяцах.')

        prefix = f'test-history:{year}:{seed}:'
        expected_assignments = len(people) * len(courses) * 2
        expected_receipts = {uuid.uuid5(uuid.NAMESPACE_URL, f'{prefix}survey:{i}') for i in range(survey_count)}
        summary = {
            'year': year, 'employees': len(people), 'courses': len(courses),
            'assignments_to_create': expected_assignments, 'survey_responses_to_create': survey_count,
            'existing_assignments': Assignment.objects.count(),
            'existing_attempts': TestAttempt.objects.count(),
            'existing_survey_responses': SurveyResponse.objects.count(),
        }
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
        if not options['apply']:
            return
        # Avoid resetting fixtures on a repeated deployment. Check the cohort
        # and published test versions as well, not just the overall row count.
        expected_pairs = {(p.user_id, c.pk, t.pk) for p in people for c, t in specifications}
        actual = Assignment.objects.all()
        matches = (
            actual.count() == expected_assignments
            and set(actual.values_list('user_id', 'course_id', 'test_version_id')) == expected_pairs
            and set(actual.values_list('assigned_at__year', 'assigned_at__month')) == {(year, 3), (year, 9)}
            and SurveyResponse.objects.count() == survey_count
            and set(SurveyResponse.objects.filter(campaign=campaign).values_list('receipt', flat=True)) == expected_receipts
        )
        if matches:
            self.stdout.write('Набор уже создан; повторный запуск ничего не изменил.')
            return
        if not options['replace_history'] and any(summary[key] for key in ('existing_assignments', 'existing_attempts', 'existing_survey_responses')):
            raise CommandError('В базе есть история. Для её полной замены после резервного копирования используйте --replace-history.')

        rng = random.Random(seed)
        rates = list(range(90, 101)) * math.ceil(len(courses) * 2 / 11)
        rng.shuffle(rates)
        assignments = []
        completed_count = 0
        coverage = []
        for index, (course, test) in enumerate(specifications):
            for cycle, month in enumerate((3, 9)):
                days = weekdays(year, month)
                start_days = days[:-1]
                assigned_day = start_days[(index + cycle * 4) % len(start_days)]
                rate = rates[index * 2 + cycle]
                passed_count = math.ceil(len(people) * rate / 100)
                passed_users = set(rng.sample([p.user_id for p in people], passed_count))
                coverage.append({'course_id': course.pk, 'month': month, 'assigned': len(people), 'completed': passed_count, 'coverage': round(100 * passed_count / len(people), 2)})
                for person in people:
                    assigned_at = timestamp(assigned_day, 9, rng.randrange(0, 55, 5))
                    passed = person.user_id in passed_users
                    completed_at = timestamp(rng.choice([day for day in days if day > assigned_day]), rng.randint(10, 16), rng.randrange(0, 60)) if passed else None
                    assignment = Assignment(
                        user_id=person.user_id, course=course, test_version=test,
                        due_date=days[-1], due_time=time(23, 59),
                        passing_score=80, attempts_allowed=2,
                        status='COMPLETED' if passed else 'OVERDUE',
                        acknowledged_at=completed_at - timedelta(minutes=rng.randint(10, 60)) if completed_at else None,
                        completed_at=completed_at,
                    )
                    assignment._fixture_assigned_at = assigned_at
                    assignment._fixture_score = rng.choice((80, 90, 100)) if passed else None
                    assignments.append(assignment)
                    completed_count += int(passed)

        department_pool = [department_code(person.department.name if person.department else '') for person in people]
        category_pool = [category_code(person.employee_type.name if person.employee_type else '') for person in people]
        responses = []
        for i in range(survey_count):
            mis_stress = survey_rng.random() < .4
            answers = [survey_rng.choice((4, 5)) for _ in range(10)]
            answers[8] = survey_rng.choice((1, 2)) if mis_stress else survey_rng.choice((4, 5))
            # The fixture models generally positive conditions; only a subset
            # reports electronic-documentation strain. No inference about staff.
            while sum(answers) < 40:
                j = survey_rng.choice([j for j in range(10) if j != 8 and answers[j] < 5])
                answers[j] += 1
            responses.append(SurveyResponse(
                campaign=campaign, receipt=uuid.uuid5(uuid.NAMESPACE_URL, f'{prefix}survey:{i}'),
                answers=answers, additional_answers={'11': '1', '12': survey_rng.choice(('1', '2')), '13': survey_rng.choice(('1', '2', '3'))},
                department=survey_rng.choice(department_pool), category=survey_rng.choice(category_pool),
                tenure=survey_rng.choice(('under1', '1to5', '6to10', 'over10')),
                schedule=survey_rng.choice(('day', 'shift', 'night')), combined_roles=survey_rng.choice(('yes', 'no')),
                comment=survey_rng.choice((
                    'Работа и отношения в коллективе устраивают. Утомляет работа с МИС и повторное заполнение электронной документации.',
                    'Хотелось бы упростить МИС и убрать дублирование записей. Остальные условия работы устраивают.',
                    'Общее впечатление от работы положительное. Основные затруднения связаны с медицинской информационной системой.',
                )) if mis_stress else survey_rng.choice((
                    'Условия работы устраивают. Поддержка коллег и руководителя достаточная.',
                    'Рабочая атмосфера доброжелательная, существенных предложений нет.',
                    'В целом работой удовлетворён(а). Взаимодействие в коллективе хорошее.',
                )),
            ))

        with transaction.atomic():
            if connection.vendor == 'postgresql':
                # Serialize a complete replacement with concurrent training
                # writes, while ordinary SELECTs continue to be served.
                with connection.cursor() as cursor:
                    cursor.execute('LOCK TABLE core_assignment, core_testattempt, core_surveyresponse IN EXCLUSIVE MODE')
            removed_attempts = TestAttempt.objects.count()
            removed_assignments = Assignment.objects.count()
            removed_responses = SurveyResponse.objects.count()
            TestAttempt.objects.all().delete()
            Assignment.objects.all().delete()
            SurveyResponse.objects.all().delete()
            Assignment.objects.bulk_create(assignments, batch_size=500)
            for assignment in assignments:
                assignment.assigned_at = assignment._fixture_assigned_at
            Assignment.objects.bulk_update(assignments, ['assigned_at'], batch_size=500)
            attempts = []
            for assignment in assignments:
                if not assignment.completed_at:
                    continue
                questions = test_questions[assignment.test_version_id]
                correct_positions = set(rng.sample(range(10), assignment._fixture_score // 10))
                snapshot = []
                for position, question in enumerate(questions):
                    expected = sorted(question.correct_indexes or [question.correct_index])
                    correct = position in correct_positions
                    if correct:
                        selected = expected
                    elif question.question_type == 'MULTIPLE':
                        selected = expected[1:]
                    else:
                        selected = [next((i for i in range(len(question.options)) if i not in expected), -1)]
                    snapshot.append({
                        'question_id': question.pk, 'question': question.text,
                        'type': question.question_type, 'options': question.options,
                        'selected': selected, 'correct': expected, 'is_correct': correct,
                        'points': 1 if correct else 0, 'max_points': 1,
                    })
                attempts.append(TestAttempt(
                    assignment=assignment, number=1, correct_answers=assignment._fixture_score // 10,
                    score=assignment._fixture_score, passed=True, answers_snapshot=snapshot,
                ))
            TestAttempt.objects.bulk_create(attempts, batch_size=500)
            for attempt in attempts:
                attempt.completed_at = attempt.assignment.completed_at
            TestAttempt.objects.bulk_update(attempts, ['completed_at'], batch_size=500)
            SurveyResponse.objects.bulk_create(responses, batch_size=100)
            AuditLog.objects.create(action='Обновлена история обучения и опроса', object_label=(
                f'{year}; удалено назначений={removed_assignments}, попыток={removed_attempts}, анкет={removed_responses}; '
                f'создано назначений={len(assignments)}, попыток={len(attempts)}, анкет={len(responses)}'
            ))
        self.stdout.write(json.dumps({
            'created_assignments': len(assignments), 'created_attempts': completed_count,
            'created_survey_responses': len(responses), 'coverage': coverage,
        }, ensure_ascii=False))
