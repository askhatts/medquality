from datetime import timedelta
from io import BytesIO, StringIO
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import Assignment, Course, Department, EmployeeType, Lesson, Profile, QualityDirection, Question, Test, TestAttempt
from .management.commands.load_medical_waste import MATERIAL_URL, TITLE


class AnalyticsFixtures:
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(name='Отделение №1')
        cls.other = Department.objects.create(name='Отделение №2')
        cls.kind, _ = EmployeeType.objects.get_or_create(name='Врач')
        cls.admin = cls.person('sysadmin', role='ADMIN', department=None)
        cls.methodist = cls.person('methodist', role='METHODIST', department=cls.other)
        cls.head = cls.person('head', role='HEAD')
        cls.employee = cls.person('employee')
        cls.employee2 = cls.person('employee2', department=cls.other)
        cls.direction, _ = QualityDirection.objects.get_or_create(title='Инфекционный контроль')
        cls.course = Course.objects.create(direction=cls.direction, title='Курс')
        cls.test = Test.objects.create(course=cls.course, title='Тест', status='PUBLISHED')
        cls.question = Question.objects.create(test=cls.test, text='Вопрос?', options=['Да', 'Нет'], correct_index=0)
        cls.batch = uuid4()

    @classmethod
    def person(cls, username, role='EMPLOYEE', department='default'):
        user = User.objects.create_user(username)
        Profile.objects.create(user=user, role=role, department=cls.dept if department == 'default' else department, employee_type=cls.kind)
        return user

    def assignment(self, user=None, batch='default', **kwargs):
        values = dict(user=user or self.employee, course=self.course, test_version=self.test,
                      due_date=timezone.localdate() + timedelta(days=10),
                      batch_key=self.batch if batch == 'default' else batch, attempts_allowed=2)
        values.update(kwargs)
        return Assignment.objects.create(**values)

    def attempt(self, assignment, score, number=1, correct=True, **kwargs):
        return TestAttempt.objects.create(
            assignment=assignment, score=score, passed=score >= 80, number=number,
            answers_snapshot=[{'question_id': self.question.pk, 'question': self.question.text,
                               'selected': [0] if correct else [1], 'is_correct': correct}],
            **kwargs,
        )

    def page(self, user=None, **params):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse('assignment_analytics'), params or {'batch': str(self.batch)})


class AssignmentAnalyticsTests(AnalyticsFixtures, TestCase):
    def test_only_latest_attempt_of_exact_batch_and_no_pending_zero_grades(self):
        a = self.assignment(status='COMPLETED')
        self.attempt(a, 20, correct=False)
        self.attempt(a, 90, number=2)
        self.assignment(user=self.employee2)
        old = self.assignment(batch=uuid4(), status='COMPLETED')
        self.attempt(old, 100)
        cancelled = self.assignment(user=self.methodist, status='CANCELLED')
        self.attempt(cancelled, 100)
        inactive = self.person('inactive')
        inactive.is_active = False
        inactive.save()
        self.assignment(user=inactive)
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['total'], 2)
        self.assertEqual(response.context['stats']['average'], 90)
        self.assertEqual(response.context['stats']['rate'], 50)
        self.assertEqual(len(response.context['questions']), 1)
        self.assertEqual(response.context['questions'][0]['total'], 1)
        self.assertEqual(response.context['questions'][0]['correct'], 1)
        self.assertIn('no-store', response['Cache-Control'])

    def test_department_states_and_questions(self):
        a = self.assignment(status='FAILED')
        self.attempt(a, 50, correct=False)
        self.assignment(user=self.employee2, due_date=timezone.localdate() - timedelta(days=1))
        self.assignment(user=self.head, status='IN_PROGRESS')
        self.assignment(user=self.methodist, status='REASSIGNED')
        data = self.page().context
        self.assertEqual(data['stats']['failed'], 1)
        self.assertEqual(data['stats']['overdue'], 1)
        self.assertEqual(data['stats']['in_progress'], 1)
        self.assertEqual(data['stats']['reassigned'], 1)
        self.assertEqual(len(data['departments']), 2)
        self.assertEqual(data['questions'][0]['rate'], 0)

    def test_roles_and_head_scope_including_selectors_and_excel(self):
        self.assignment()
        foreign_batch = uuid4()
        self.assignment(user=self.employee2, batch=foreign_batch)
        head = self.page(self.head)
        self.assertEqual(head.context['stats']['total'], 1)
        self.assertEqual(len(head.context['events']), 1)
        self.assertEqual(self.page(self.head, batch=str(foreign_batch)).status_code, 404)
        self.assertEqual(self.page(self.methodist).status_code, 200)
        self.assertEqual(self.page(self.employee).status_code, 403)
        self.assertEqual(self.client.get(reverse('assignment_analytics_excel')).status_code, 403)
        self.head.profile.department = None
        self.head.profile.save()
        self.client.force_login(self.head)
        empty = self.client.get(reverse('assignment_analytics'))
        self.assertEqual(empty.context['stats']['total'], 0)
        self.assertEqual(empty.context['events'], [])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('assignment_analytics')).status_code, 302)

    def test_shared_batch_is_scoped_to_head_department(self):
        a = self.assignment(status='COMPLETED')
        b = self.assignment(user=self.employee2, status='COMPLETED')
        self.attempt(a, 80)
        self.attempt(b, 100)
        response = self.page(self.head)
        self.assertEqual(response.context['stats']['average'], 80)
        self.assertNotContains(response, self.other.name)
        excel = self.client.get(reverse('assignment_analytics_excel'), {'batch': str(self.batch)})
        book = load_workbook(BytesIO(excel.content))
        self.assertEqual(book['Сотрудники'].max_row, 2)
        self.assertEqual(book['Вопросы'].max_row, 2)
        self.assertNotIn(self.other.name, str(list(book['Отделения'].values)))

    def test_legacy_grouping_does_not_change_records(self):
        a = self.assignment(batch=None)
        b = self.assignment(batch=None, status='COMPLETED')
        self.attempt(b, 90)
        day = timezone.localtime(b.assigned_at).date().isoformat()
        response = self.page(batch=f'legacy:{self.course.pk}:{day}')
        self.assertContains(response, 'Архивные записи')
        self.assertEqual(response.context['stats']['total'], 1)
        self.assertEqual(response.context['stats']['average'], 90)
        a.refresh_from_db()
        self.assertIsNone(a.batch_key)
        self.assertEqual(a.status, 'ASSIGNED')
        self.assertEqual(Assignment.objects.count(), 2)

    def test_excel_has_numeric_values_dates_and_safe_text(self):
        self.employee.first_name = '=DANGEROUS()'
        self.employee.save()
        a = self.assignment(status='COMPLETED')
        self.attempt(a, 80)
        self.page()
        response = self.client.get(reverse('assignment_analytics_excel'), {'batch': str(self.batch)})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.content))
        self.assertEqual(book.sheetnames, ['Параметры', 'Отделения', 'Сотрудники', 'Вопросы'])
        row = list(book['Сотрудники'].values)[1]
        self.assertTrue(row[0].startswith("'="))
        self.assertEqual(row[10], 80)
        self.assertEqual(row[7].date(), timezone.localdate())
        self.assertEqual(book['Отделения']['J2'].value, 80)
        self.assertEqual(book['Сотрудники'].freeze_panes, 'A2')

    def test_invalid_parameters_are_404_not_500(self):
        self.assignment()
        for params in ({'course': 'bad'}, {'batch': 'bad'}, {'course': '999999'}, {'batch': 'legacy:bad:2026-11-20'}):
            self.assertEqual(self.page(**params).status_code, 404)

    def test_timeout_and_legacy_aggregate_omitted_from_question_detail(self):
        a = self.assignment()
        TestAttempt.objects.create(assignment=a, number=1, score=0, passed=False, answers_snapshot=[{'timed_out': True}])
        self.assertEqual(self.page().context['questions'], [])

    def test_assignment_creation_has_one_batch_and_preserves_old_results(self):
        old = self.assignment(status='COMPLETED')
        attempt = self.attempt(old, 80)
        self.client.force_login(self.methodist)
        payload = {'course': self.course.pk, 'due_date': '2030-01-01', 'passing_score': 80,
                   'attempts_allowed': 2, 'departments': [self.dept.pk], 'users': [self.employee.pk, self.employee2.pk]}
        response = self.client.post(reverse('assignment_new'), payload)
        self.assertEqual(response.status_code, 302)
        assignments = Assignment.objects.exclude(pk=old.pk)
        self.assertEqual(assignments.count(), 3)  # head, employee and explicit employee2
        batches = set(assignments.values_list('batch_key', flat=True))
        self.assertEqual(len(batches), 1)
        self.assertNotIn(None, batches)
        self.client.post(reverse('assignment_new'), payload)
        self.assertEqual(Assignment.objects.filter(batch_key__in=batches, status='REASSIGNED').count(), 3)
        self.assertEqual(Assignment.objects.exclude(pk=old.pk).values('batch_key').distinct().count(), 2)
        old.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(old.status, 'COMPLETED')
        self.assertEqual(attempt.score, 80)

    def test_bad_assignment_settings_cannot_create_records(self):
        self.client.force_login(self.admin)
        payload = {'course': self.course.pk, 'due_date': '2030-01-01', 'passing_score': '101', 'attempts_allowed': '2', 'users': [self.employee.pk]}
        self.assertEqual(self.client.post(reverse('assignment_new'), payload).status_code, 400)
        payload['passing_score'] = '80'
        payload['departments'] = ['bad']
        self.assertEqual(self.client.post(reverse('assignment_new'), payload).status_code, 400)
        self.assertFalse(Assignment.objects.exists())


class MedicalWasteImportTests(AnalyticsFixtures, TestCase):
    def run_command(self, **kwargs):
        output = StringIO()
        call_command('load_medical_waste', due_date='2030-11-24', stdout=output, **kwargs)
        return output.getvalue()

    def test_import_preview_does_not_write(self):
        output = self.run_command(assign=True)
        self.assertIn('Предварительный просмотр', output)
        self.assertFalse(Course.objects.filter(title=TITLE).exists())
        self.assertFalse(Assignment.objects.exists())

    def test_import_links_questions_assignments_idempotent_and_preserves_results(self):
        inactive = self.person('inactive-command')
        inactive.is_active = False
        inactive.save()
        self.run_command(apply=True, assign=True)
        course = Course.objects.get(title=TITLE)
        self.assertEqual(course.direction, self.direction)
        lesson = Lesson.objects.get(course=course)
        self.assertEqual(lesson.url, MATERIAL_URL)
        self.assertFalse(lesson.file)
        self.assertNotIn('docs.google.com', lesson.url)
        test = course.tests.get(is_current=True)
        self.assertEqual(test.questions.count(), 10)
        self.assertEqual(test.time_limit_minutes, 10)
        assignments = Assignment.objects.filter(course=course)
        self.assertEqual(assignments.count(), 4)  # methodist, head and two employees
        self.assertFalse(assignments.filter(user=self.admin).exists())
        self.assertFalse(assignments.filter(user=inactive).exists())
        self.assertEqual(assignments.values('batch_key').distinct().count(), 1)
        a = assignments.first()
        a.status = 'COMPLETED'
        a.save()
        attempt = self.attempt(a, 90)
        self.run_command(apply=True, assign=True)
        self.assertEqual(assignments.count(), 4)
        self.assertEqual(course.tests.count(), 1)
        self.assertEqual(a.attempts.get().score, 90)
        a.refresh_from_db()
        self.assertEqual(a.status, 'COMPLETED')
        self.assertEqual(a.due_date.isoformat(), '2030-11-24')
        self.assertEqual(a.due_time.strftime('%H:%M'), '23:59')
        self.assertEqual((a.passing_score, a.attempts_allowed), (80, 2))
        self.assertEqual(TestAttempt.objects.count(), 1)

    def test_expired_deadline_rejected_without_writes(self):
        with self.assertRaises(CommandError):
            call_command('load_medical_waste', due_date='2000-01-01', apply=True, assign=True, stdout=StringIO())
        self.assertFalse(Course.objects.filter(title=TITLE).exists())

    def test_imported_answer_key_scores_all_ten_questions_correctly(self):
        self.run_command(apply=True, assign=True)
        course = Course.objects.get(title=TITLE)
        test = course.tests.get(is_current=True)
        self.client.force_login(self.employee)
        start = self.client.post(reverse('test_start', args=[course.pk]))
        self.assertEqual(start.status_code, 302)
        answers = {f'q{q.pk}': str(q.correct_index) for q in test.questions.all()}
        response = self.client.post(reverse('test', args=[course.pk]), answers)
        self.assertContains(response, 'Тест пройден')
        assignment = Assignment.objects.get(course=course, user=self.employee)
        attempt = assignment.attempts.get()
        self.assertEqual(attempt.score, 100)
        self.assertEqual(attempt.correct_answers, 10)
        self.assertEqual(assignment.status, 'COMPLETED')

    def test_reimport_content_change_creates_version_without_editing_used_test(self):
        from .management.commands.load_medical_waste import QUESTIONS
        self.run_command(apply=True, assign=True)
        course = Course.objects.get(title=TITLE)
        old_test = course.tests.get(is_current=True)
        assignment = Assignment.objects.get(course=course, user=self.employee)
        old_text = old_test.questions.first().text
        changed = list(QUESTIONS)
        text, choices, correct, explanation = changed[0]
        changed[0] = (text + ' Уточнение.', choices, correct, explanation)
        with patch('core.management.commands.load_medical_waste.QUESTIONS', changed):
            self.run_command(apply=True, assign=True)
        self.assertEqual(course.tests.count(), 2)
        old_test.refresh_from_db()
        assignment.refresh_from_db()
        self.assertFalse(old_test.is_current)
        self.assertEqual(old_test.questions.first().text, old_text)
        self.assertEqual(assignment.test_version_id, old_test.pk)
