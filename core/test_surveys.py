import uuid
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Assignment, Course, Profile, SurveyCampaign, SurveyResponse, TestAttempt
from .surveys import survey_analytics


class AnonymousSurveyTests(TestCase):
    def setUp(self):
        self.campaign = SurveyCampaign.objects.create(slug='wellbeing', title='Опрос ЦЯМиО', audience_size=100)
        self.admin = User.objects.create_user('survey-admin', password='test-password')
        Profile.objects.create(user=self.admin, role='ADMIN')
        self.employee = User.objects.create_user('survey-employee', password='test-password')
        Profile.objects.create(user=self.employee, role='EMPLOYEE')

    def response(self, answers=None, **kwargs):
        return SurveyResponse.objects.create(campaign=self.campaign, receipt=uuid.uuid4(), answers=answers or [5] * 10, **kwargs)

    def submit_payload(self, client):
        page = client.get(reverse('survey_public'))
        return {
            'receipt': page.context['form']['receipt'].value(),
            'consent': 'on', **{f'q{i}': '5' for i in range(1, 11)},
        }

    def test_public_submit_is_anonymous_idempotent_and_does_not_create_assignments(self):
        payload = self.submit_payload(self.client)
        self.assertFalse(self.client.get(reverse('survey_public')).context['already_submitted'])
        response = self.client.post(reverse('survey_public'), payload, follow=True)
        self.assertContains(response, 'Спасибо за участие')
        self.client.post(reverse('survey_public'), payload)
        self.assertEqual(SurveyResponse.objects.count(), 1)
        row = SurveyResponse.objects.get()
        self.assertEqual(row.answers, [5] * 10)
        self.assertEqual(row.department, '')
        self.assertEqual(Assignment.objects.count(), 0)
        fields = {field.name for field in SurveyResponse._meta.fields}
        self.assertTrue(fields.isdisjoint({'user', 'profile', 'ip', 'session', 'created_at'}))
        self.client.force_login(self.employee)
        page = self.client.get(reverse('survey_public'))
        self.assertNotContains(page, self.employee.username)
        self.assertNotContains(page, 'side-nav')

    def test_invalid_missing_answers_or_consent_are_not_saved(self):
        payload = self.submit_payload(self.client)
        payload['q6'] = '6'
        response = self.client.post(reverse('survey_public'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SurveyResponse.objects.exists())
        payload['q6'] = '1'
        payload.pop('consent')
        self.client.post(reverse('survey_public'), payload)
        self.assertFalse(SurveyResponse.objects.exists())

    def test_receipt_cannot_be_reused_from_another_browser(self):
        payload = self.submit_payload(self.client)
        other = Client()
        self.submit_payload(other)
        self.assertContains(other.post(reverse('survey_public'), payload), 'Не удалось подтвердить отправку')
        self.assertFalse(SurveyResponse.objects.exists())

    def test_closed_campaign_cannot_accept_a_stale_form(self):
        payload = self.submit_payload(self.client)
        self.campaign.active = False
        self.campaign.save(update_fields=['active'])
        self.assertContains(self.client.post(reverse('survey_public'), payload), 'нет открытого опроса')
        self.assertFalse(SurveyResponse.objects.exists())

    def test_public_submission_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        payload = self.submit_payload(client)
        self.assertEqual(client.post(reverse('survey_public'), payload).status_code, 403)
        self.assertFalse(SurveyResponse.objects.exists())

    def test_scores_use_original_direction_and_additional_answers_do_not_affect_sum(self):
        for index in range(10):
            self.response(
                answers=[5, 3 if index < 2 else 5, 5, 5, 5, 4, 4, 4, 1 if index < 4 else 5, 4],
                additional_answers={'11': '3', '12': '5'},
            )
        data = survey_analytics(self.campaign)
        self.assertEqual(data['total'], 44)
        self.assertEqual(data['support'], 24.6)
        self.assertEqual(data['pace'], 19.4)
        self.assertEqual(data['burnout'], 20)
        self.assertEqual(data['mis_stress'], 40)
        self.assertEqual(data['response_ratio'], 10)

    def test_small_groups_and_small_complements_are_suppressed(self):
        for index in range(6):
            self.response(department='clinic' if index < 5 else 'surgery')
        data = survey_analytics(self.campaign)
        self.assertTrue(data['visible'])
        department_groups = [g for g in data['groups'] if g['dimension'] == 'Подразделение']
        self.assertFalse(any(g['visible'] for g in department_groups))

    def test_analytics_is_admin_only_and_never_contains_raw_comments(self):
        for _ in range(5):
            self.response(comment='СЕКРЕТНЫЙ ИНДИВИДУАЛЬНЫЙ КОММЕНТАРИЙ')
        self.assertEqual(self.client.get(reverse('survey_dashboard')).status_code, 302)
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse('survey_dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('survey_pdf', args=[self.campaign.pk])).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('survey_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'СЕКРЕТНЫЙ ИНДИВИДУАЛЬНЫЙ КОММЕНТАРИЙ')
        self.assertContains(response, 'Скачать PDF с графиками')

    def test_pdf_with_charts_and_empty_state_both_render(self):
        self.client.force_login(self.admin)
        url = reverse('survey_pdf', args=[self.campaign.pk])
        empty = self.client.get(url)
        self.assertEqual(empty['Content-Type'], 'application/pdf')
        self.assertTrue(empty.content.startswith(b'%PDF-'))
        for _ in range(5):
            self.response(department='clinic', comment='МИС и электронные системы')
        full = self.client.get(url)
        self.assertEqual(full.status_code, 200)
        self.assertTrue(full.content.startswith(b'%PDF-'))
        self.assertGreater(len(full.content), len(empty.content))
        self.assertIn('no-store', full['Cache-Control'])

    def test_settings_cannot_have_an_end_date_before_start(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('survey_dashboard'), {
            'title': self.campaign.title, 'active': 'on',
            'opens_on': timezone.localdate().isoformat(),
            'closes_on': (timezone.localdate() - timedelta(days=1)).isoformat(),
            'audience_size': 100,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['settings_form'].errors)

    def test_prepare_command_is_idempotent_and_never_creates_training_results_or_survey_responses(self):
        counts_before = (Assignment.objects.count(), TestAttempt.objects.count(), SurveyResponse.objects.count())
        call_command('prepare_ethics_and_survey', stdout=StringIO())
        course = Course.objects.get(title='Кодекс этики ЦЯМиО')
        test = course.tests.get(is_current=True)
        questions_before = list(test.questions.values_list('id', flat=True))
        call_command('prepare_ethics_and_survey', stdout=StringIO())
        self.assertEqual(list(test.questions.values_list('id', flat=True)), questions_before)
        self.assertEqual(len(questions_before), 10)
        self.assertEqual(course.lessons.count(), 1)
        self.assertEqual(course.internal_documents.count(), 1)
        self.assertEqual((Assignment.objects.count(), TestAttempt.objects.count(), SurveyResponse.objects.count()), counts_before)
