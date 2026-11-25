from datetime import timedelta
from io import BytesIO, StringIO

from django.core.management import call_command, CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .assignment_queries import pending_course_assignments
from .management.commands.recover_training_results import recovery_scope
from .models import Assignment, AuditLog, ResultRecovery, SurveyResponse, TestAttempt
from .test_assignment_analytics import AnalyticsFixtures
from .views import editable_test, report_entry, visible_assignments


class ResultRecoveryTests(AnalyticsFixtures, TestCase):
    reason = 'По подтверждению администратора: результат утрачен при сбое интернет-соединения.'

    def command(self, **kwargs):
        options = dict(courses=[self.course.pk], month=timezone.localdate().strftime('%Y-%m'),
                       score=90, actor=self.admin.username, reason=self.reason, stdout=StringIO())
        options.update(kwargs)
        call_command('recover_training_results', **options)
        return options['stdout'].getvalue()

    def apply(self, **kwargs):
        return self.command(apply=True, confirm_outage=True, **kwargs)

    def test_preview_does_not_write_and_confirmation_is_required(self):
        a = self.assignment()
        self.assertIn('Предварительный просмотр', self.command())
        with self.assertRaises(CommandError):
            self.command(apply=True)
        a.refresh_from_db()
        self.assertEqual(a.status, 'ASSIGNED')
        self.assertFalse(ResultRecovery.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_recovery_preserves_saved_attempts_and_does_not_invent_dates_or_answers(self):
        a = self.assignment(status='FAILED', current_attempt_started_at=timezone.now())
        self.attempt(a, 40, correct=False)
        before = list(TestAttempt.objects.values())
        survey_before = list(SurveyResponse.objects.values())
        timer = a.current_attempt_started_at
        self.apply()
        a = visible_assignments(self.admin).get(pk=a.pk)
        self.assertEqual(a.status, 'COMPLETED')
        self.assertIsNone(a.completed_at)
        self.assertIsNone(a.current_attempt_started_at)
        self.assertEqual(list(TestAttempt.objects.values()), before)
        self.assertEqual(list(SurveyResponse.objects.values()), survey_before)
        row = report_entry(a)
        self.assertEqual(row['score'], 90)
        self.assertEqual(row['last_attempt'].score, 40)
        self.assertIsNone(row['result_at'])
        self.assertTrue(row['recovered'])
        self.assertEqual(row['attempts_used'], 1)
        record = a.recovered_result
        self.assertEqual(record.previous_status, 'FAILED')
        self.assertEqual(record.previous_attempt_started_at, timer)
        self.assertEqual(record.recorded_by, self.admin)
        self.assertEqual(record.reason, self.reason)
        self.assertEqual(AuditLog.objects.get().action, 'Восстановлены результаты после сбоя')

    def test_known_completion_date_remains_and_repeat_is_idempotent(self):
        completed = timezone.now() - timedelta(hours=1)
        a = self.assignment(status='COMPLETED', completed_at=completed)
        self.attempt(a, 100)
        self.apply()
        before = list(ResultRecovery.objects.values())
        self.assertIn('Восстановлено 0', self.apply())
        self.assertEqual(list(ResultRecovery.objects.values()), before)
        self.assertEqual(AuditLog.objects.count(), 1)
        a = visible_assignments(self.admin).get(pk=a.pk)
        self.assertEqual(a.completed_at, completed)
        self.assertEqual(report_entry(a)['result_at'], completed)
        self.assertEqual(a.attempts.get().score, 100)
        with self.assertRaises(CommandError):
            self.apply(score=80)
        self.assertEqual(list(ResultRecovery.objects.values()), before)

    def test_scope_does_not_resurrect_cancelled_or_reassigned_or_alter_old_history(self):
        old = self.assignment()
        Assignment.objects.filter(pk=old.pk).update(assigned_at=timezone.now() - timedelta(days=400))
        chosen = self.assignment()
        cancelled_old = self.assignment(user=self.head)
        self.assignment(user=self.head, status='CANCELLED')
        replaced = self.assignment(user=self.methodist, status='REASSIGNED')
        self.employee2.is_active = False
        self.employee2.save()
        inactive = self.assignment(user=self.employee2)
        self.apply()
        self.assertEqual(list(ResultRecovery.objects.values_list('assignment_id', flat=True)), [chosen.pk])
        for a in (old, cancelled_old, replaced, inactive):
            original = a.status
            a.refresh_from_db()
            self.assertEqual(a.status, original)

    def test_invalid_input_and_low_score_rejected_without_partial_writes(self):
        self.assignment()
        self.assignment(user=self.head, passing_score=95)
        with self.assertRaises(CommandError):
            self.apply()
        for options in ({'score': 101}, {'month': 'bad'}, {'month': '2099-01'},
                        {'actor': self.methodist.username}, {'reason': 'short'}, {'courses': [999999]}):
            with self.assertRaises(CommandError):
                self.apply(**options)
        self.assertFalse(ResultRecovery.objects.exists())
        self.assertFalse(Assignment.objects.filter(status='COMPLETED').exists())

    def test_students_see_recovered_score_and_cannot_start_a_new_attempt(self):
        a = self.assignment()
        self.apply()
        self.client.force_login(self.employee)
        response = self.client.get(reverse('courses'), {'tab': 'completed'})
        self.assertContains(response, '90%')
        self.assertContains(response, 'Восстановлено администратором')
        self.assertEqual(response.context['notification_count'], 0)
        self.assertFalse(pending_course_assignments(self.employee).exists())
        course = self.client.get(reverse('course', args=[self.course.pk]))
        self.assertContains(course, '90%')
        self.assertContains(course, 'Результат восстановлен администратором')
        self.assertEqual(self.client.post(reverse('test_start', args=[self.course.pk])).status_code, 302)
        self.assertFalse(TestAttempt.objects.exists())
        self.client.force_login(self.employee2)
        self.assertEqual(self.client.get(reverse('course', args=[self.course.pk]), {'assignment': a.pk}).status_code, 404)

    def test_reports_excel_and_analytics_use_recovered_score_with_source_not_question_details(self):
        a = self.assignment(status='FAILED')
        self.attempt(a, 40, correct=False)
        self.apply()
        response = self.page()
        self.assertEqual(response.context['stats']['average'], 90)
        self.assertEqual(response.context['stats']['rate'], 100)
        self.assertEqual(response.context['questions'], [])
        self.assertContains(response, 'Восстановлено администратором')
        excel = self.client.get(reverse('assignment_analytics_excel'), {'batch': str(self.batch)})
        book = load_workbook(BytesIO(excel.content))
        row = list(book['Сотрудники'].values)[1]
        self.assertEqual(row[10], 90)
        self.assertIsNone(row[11])
        self.assertEqual(row[15], 'Восстановлено администратором')
        self.assertIsNotNone(row[16])
        self.assertEqual(book['Вопросы'].max_row, 1)
        for view in ('course', 'matrix', 'history'):
            report = self.client.get(reverse('reports'), {'course': self.course.pk, 'view': view})
            self.assertContains(report, '90%')
            self.assertContains(report, 'Восстановлено администратором')
        book = load_workbook(BytesIO(self.client.get(reverse('report_excel'), {'course': self.course.pk}).content))
        row = list(book['Журнал назначений'].values)[1]
        self.assertEqual(row[9], 90)
        self.assertEqual(row[14], 'Восстановлено администратором')
        self.client.force_login(self.head)
        self.assertEqual(self.client.get(reverse('assignment_analytics')).context['stats']['total'], 1)
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse('assignment_analytics')).status_code, 403)

    def test_published_test_with_recovery_is_frozen_even_without_saved_attempts(self):
        self.assignment()
        self.apply()
        changed = editable_test(self.course)
        self.assertNotEqual(changed.pk, self.test.pk)
        self.assertEqual(changed.status, 'DRAFT')
        self.assertEqual(Assignment.objects.get().test_version_id, self.test.pk)

    def test_unrecovered_grade_is_unchanged(self):
        a = self.assignment(status='COMPLETED')
        attempt = self.attempt(a, 80)
        row = report_entry(visible_assignments(self.admin).get(pk=a.pk))
        self.assertEqual(row['score'], 80)
        self.assertEqual(row['result_at'], attempt.completed_at)
        self.assertEqual(row['result_source'], 'Сохранённая попытка')
        self.assertFalse(row['recovered'])
