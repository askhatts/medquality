import json
import uuid
from collections import defaultdict
from datetime import date
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .models import Assignment, AuditLog, Course, Profile, QualityDirection, Question, SurveyCampaign, SurveyResponse, Test, TestAttempt
from .surveys import survey_analytics
from .views import visible_assignments


class HistoryReplacementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(username='sysadmin')
        Profile.objects.create(user=cls.admin, role='ADMIN')
        people = [User.objects.create(username=f'staff-{i}') for i in range(20)]
        Profile.objects.bulk_create([Profile(user=user, role='EMPLOYEE') for user in people])
        cls.people_ids = [user.pk for user in people]
        cls.direction = QualityDirection.objects.create(title='Тестовое направление')
        cls.courses = []
        for i in range(2):
            course = Course.objects.create(title=f'Курс {i}', direction=cls.direction)
            test = Test.objects.create(course=course, title=f'Тест {i}', status='PUBLISHED')
            Question.objects.bulk_create([Question(test=test, text=f'Вопрос {j}', options=['Да', 'Нет'], correct_indexes=[0], order=j + 1) for j in range(10)])
            cls.courses.append(course)
        cls.campaign = SurveyCampaign.objects.create(slug='staff-wellbeing-2026-11', title='Опрос', audience_size=20)
        cls.old_assignment = Assignment.objects.create(user=people[0], course=cls.courses[0], due_date=date(2026, 2, 27))
        cls.old_attempt = TestAttempt.objects.create(assignment=cls.old_assignment, number=1, correct_answers=1, score=10, passed=False)
        cls.old_response = SurveyResponse.objects.create(campaign=cls.campaign, receipt=uuid.uuid4(), answers=[1] * 10)

    def generate(self, **kwargs):
        output = StringIO()
        call_command('replace_test_history', apply=True, replace_history=True, survey_count=110, stdout=output, **kwargs)
        return output.getvalue()

    def test_preview_and_missing_explicit_replacement_do_not_change_history(self):
        output = StringIO()
        call_command('replace_test_history', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['assignments_to_create'], 80)
        self.assertEqual(Assignment.objects.count(), 1)
        self.assertEqual(TestAttempt.objects.count(), 1)
        self.assertEqual(SurveyResponse.objects.count(), 1)
        with self.assertRaises(CommandError):
            call_command('replace_test_history', apply=True, stdout=StringIO())
        self.assertTrue(Assignment.objects.filter(pk=self.old_assignment.pk).exists())

    def test_full_replacement_has_two_cycles_weekdays_valid_scores_and_coverage(self):
        self.generate()
        self.assertFalse(Assignment.objects.filter(pk=self.old_assignment.pk).exists())
        self.assertFalse(TestAttempt.objects.filter(pk=self.old_attempt.pk).exists())
        self.assertFalse(SurveyResponse.objects.filter(pk=self.old_response.pk).exists())
        self.assertEqual(Assignment.objects.count(), 80)
        self.assertEqual(User.objects.count(), 21)
        self.assertEqual(Course.objects.count(), 2)
        self.assertFalse(Assignment.objects.filter(user=self.admin).exists())
        groups = defaultdict(list)
        pairs = defaultdict(list)
        for assignment in Assignment.objects.prefetch_related('attempts'):
            local = timezone.localtime(assignment.assigned_at)
            self.assertEqual(local.year, 2026)
            self.assertIn(local.month, (3, 9))
            self.assertLess(local.weekday(), 5)
            self.assertFalse(assignment.is_demo)
            self.assertIsNone(assignment.demo_key)
            groups[(assignment.course_id, local.month)].append(assignment)
            pairs[(assignment.user_id, assignment.course_id)].append(local.month)
            attempts = list(assignment.attempts.all())
            if assignment.status == 'COMPLETED':
                self.assertEqual(len(attempts), 1)
                attempt = attempts[0]
                self.assertIn(attempt.score, (80, 90, 100))
                self.assertEqual(attempt.correct_answers, attempt.score // 10)
                self.assertEqual(attempt.completed_at, assignment.completed_at)
                completed = timezone.localtime(attempt.completed_at)
                self.assertEqual(completed.month, local.month)
                self.assertLess(completed.weekday(), 5)
                self.assertGreater(attempt.completed_at, assignment.assigned_at)
                self.assertLessEqual(attempt.completed_at, assignment.deadline)
                self.assertEqual(sum(row['points'] for row in attempt.answers_snapshot), attempt.correct_answers)
                self.assertEqual(len(attempt.answers_snapshot), 10)
                self.assertFalse(any('generated' in row or 'seed' in row for row in attempt.answers_snapshot))
            else:
                self.assertEqual(assignment.status, 'OVERDUE')
                self.assertEqual(attempts, [])
        self.assertTrue(all(sorted(months) == [3, 9] for months in pairs.values()))
        for members in groups.values():
            percent = sum(a.status == 'COMPLETED' for a in members) / len(members) * 100
            self.assertGreaterEqual(percent, 90)
            self.assertLessEqual(percent, 100)
        self.assertEqual(set(TestAttempt.objects.values_list('score', flat=True)), {80, 90, 100})
        self.assertTrue(AuditLog.objects.filter(action='Обновлена история обучения и опроса').exists())

    def test_generated_survey_is_positive_varied_anonymous_and_reproducible(self):
        self.generate()
        self.assertEqual(SurveyResponse.objects.count(), 110)
        self.assertNotIn('is_demo', {field.name for field in SurveyResponse._meta.fields})
        data = survey_analytics(self.campaign)
        self.assertEqual(data['count'], 110)
        self.assertEqual(data['burnout'], 0)
        self.assertEqual(data['favourable'], 100)
        self.assertGreater(data['mis_stress'], 0)
        self.assertLess(data['mis_stress'], 100)
        self.assertGreater(len({tuple(r.answers) for r in self.campaign.responses.all()}), 20)
        original_ids = set(Assignment.objects.values_list('pk', flat=True))
        receipt_ids = set(SurveyResponse.objects.values_list('pk', flat=True))
        message = self.generate()
        self.assertIn('повторный запуск ничего не изменил', message)
        self.assertEqual(set(Assignment.objects.values_list('pk', flat=True)), original_ids)
        self.assertEqual(set(SurveyResponse.objects.values_list('pk', flat=True)), receipt_ids)

    def test_invalid_test_preflight_does_not_delete_existing_records(self):
        self.courses[0].tests.first().questions.first().delete()
        with self.assertRaises(CommandError):
            self.generate()
        self.assertEqual(Assignment.objects.count(), 1)
        self.assertEqual(TestAttempt.objects.count(), 1)
        self.assertEqual(SurveyResponse.objects.count(), 1)

    def test_report_attempt_query_does_not_load_answer_snapshots(self):
        self.generate()
        assignments = list(visible_assignments(self.admin))
        attempt = next(a.attempts.all()[0] for a in assignments if a.status == 'COMPLETED')
        self.assertIn('answers_snapshot', attempt.get_deferred_fields())
