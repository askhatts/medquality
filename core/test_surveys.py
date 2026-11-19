import uuid
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Assignment, Course, Profile, SurveyCampaign, SurveyResponse, TestAttempt
from .surveys import QUESTIONS, burnout_self_assessment, survey_analytics


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

    def test_personal_burnout_result_reflects_q2_not_the_total_score(self):
        labels = {
            1: 'Выраженное эмоциональное истощение по самооценке',
            2: 'Сохраняющиеся признаки выгорания по самооценке',
            3: 'Начальные признаки выгорания по самооценке',
            4: 'Стресс без ощущения выгорания по самооценке',
            5: 'Признаки выгорания не отмечены по самооценке',
        }
        for value, label in labels.items():
            with self.subTest(value=value):
                browser = Client()
                payload = self.submit_payload(browser)
                payload.update({f'q{i}': '1' for i in range(1, 11)})
                payload['q2'] = str(value)
                page = browser.post(reverse('survey_public'), payload, follow=True)
                self.assertContains(page, 'id="personal-burnout-result"')
                self.assertContains(page, label)
                self.assertContains(page, QUESTIONS[1][1][value - 1])
                self.assertContains(page, 'не медицинский диагноз')
                self.assertEqual(page.context['personal_result']['label'], label)
                self.assertContains(browser.get(reverse('survey_public')), label)
                self.assertIn('no-store', page['Cache-Control'])
                self.assertEqual(page['X-Robots-Tag'], 'noindex, noarchive')
        self.assertEqual(SurveyResponse.objects.count(), 5)
        for answers in (None, {}, [], [5], [5, 0] + [5] * 8, [5, '3'] + [5] * 8, [5, True] + [5] * 8):
            self.assertIsNone(burnout_self_assessment(answers))

    def test_personal_result_is_not_accessible_by_query_or_from_other_browsers(self):
        browser = Client()
        payload = self.submit_payload(browser)
        payload['q2'] = '1'
        browser.post(reverse('survey_public'), payload)
        row = SurveyResponse.objects.get()
        self.assertContains(browser.get(reverse('survey_public')), 'id="personal-burnout-result"')
        for user in (None, self.employee, self.admin):
            outsider = Client()
            if user:
                outsider.force_login(user)
            for query in ({}, {'receipt': str(row.receipt), 'id': str(row.pk)}):
                page = outsider.get(reverse('survey_public'), query)
                self.assertNotContains(page, 'id="personal-burnout-result"')
                self.assertIsNone(page.context['personal_result'])
        cookie = f'wellbeing_{self.campaign.pk}'
        browser.cookies[cookie] = 'tampered-cookie'
        self.assertNotContains(browser.get(reverse('survey_public')), 'id="personal-burnout-result"')

    def test_hide_personal_result_keeps_answers_and_duplicate_protection(self):
        payload = self.submit_payload(self.client)
        payload['q2'] = '2'
        self.client.post(reverse('survey_public'), payload)
        original = SurveyResponse.objects.get().answers
        page = self.client.post(reverse('survey_public'), {'action': 'hide_result'}, follow=True)
        self.assertContains(page, 'Личный результат скрыт')
        self.assertNotContains(page, 'id="personal-burnout-result"')
        self.assertTrue(page.context['already_submitted'])
        self.assertTrue(page.context['personal_result_hidden'])
        payload['q2'] = '5'
        self.client.post(reverse('survey_public'), payload)
        self.assertEqual(SurveyResponse.objects.count(), 1)
        self.assertEqual(SurveyResponse.objects.get().answers, original)
        self.assertTrue(self.client.get(reverse('survey_public')).context['personal_result_hidden'])

    def test_hidden_personal_result_does_not_hide_next_halfyear_result(self):
        self.campaign.opens_on = date(2025, 1, 1)
        self.campaign.save(update_fields=['opens_on'])
        with patch('django.utils.timezone.localdate', return_value=date(2026, 6, 30)):
            self.client.post(reverse('survey_public'), self.submit_payload(self.client))
            self.client.post(reverse('survey_public'), {'action': 'hide_result'})
        with patch('django.utils.timezone.localdate', return_value=date(2026, 7, 1)):
            payload = self.submit_payload(self.client)
            payload['q2'] = '3'
            page = self.client.post(reverse('survey_public'), payload, follow=True)
            self.assertContains(page, 'Начальные признаки выгорания по самооценке')
            self.assertFalse(page.context['personal_result_hidden'])
        self.assertEqual(SurveyResponse.objects.count(), 2)

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

    def https_submission(self, *, origin=None, referer=None):
        client = Client(enforce_csrf_checks=True)
        url = reverse('survey_public')
        page = client.get(url, secure=True)
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertContains(page, '<meta name="referrer" content="same-origin">')
        payload = {
            'receipt': page.context['form']['receipt'].value(),
            'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
            'consent': 'on', **{f'q{i}': '4' for i in range(1, 11)},
        }
        headers = {}
        if origin is not None:
            headers['HTTP_ORIGIN'] = origin
        if referer is not None:
            headers['HTTP_REFERER'] = referer
        return client, client.post(url, payload, secure=True, **headers)

    def test_https_browser_submission_saves_and_shows_confirmation_once(self):
        client, response = self.https_submission(origin='https://testserver')
        self.assertRedirects(response, reverse('survey_public'))
        self.assertEqual(SurveyResponse.objects.count(), 1)
        self.assertEqual(SurveyResponse.objects.get().answers, [4] * 10)
        page = client.get(reverse('survey_public'), secure=True)
        self.assertTrue(page.context['already_submitted'])

    def test_https_browser_without_origin_accepts_same_origin_referer(self):
        _, response = self.https_submission(referer='https://testserver/quality/survey/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SurveyResponse.objects.count(), 1)

    def test_hiding_personal_result_also_requires_csrf(self):
        client, _ = self.https_submission(origin='https://testserver')
        url = reverse('survey_public')
        response = client.post(url, {'action': 'hide_result'}, secure=True, HTTP_ORIGIN='https://testserver')
        self.assertEqual(response.status_code, 403)
        self.assertContains(client.get(url, secure=True), 'id="personal-burnout-result"')
        response = client.post(url, {
            'action': 'hide_result', 'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
        }, secure=True, HTTP_ORIGIN='https://testserver')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.get(url, secure=True).context['personal_result_hidden'])
        self.assertEqual(SurveyResponse.objects.count(), 1)

    def test_https_browser_rejects_null_and_foreign_origins_even_with_token(self):
        for origin in ('null', 'https://untrusted.example'):
            with self.subTest(origin=origin):
                _, response = self.https_submission(origin=origin)
                self.assertEqual(response.status_code, 403)
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

    def test_analytics_never_breaks_down_employee_categories(self):
        for index in range(6):
            self.response(category='doctor' if index < 5 else 'nurse')
        data = survey_analytics(self.campaign)
        self.assertTrue(data['visible'])
        self.assertEqual(data['groups'], [])

    def test_demographics_are_excluded_from_analytics_and_both_halfyear_pdfs(self):
        from reportlab.platypus import Paragraph

        for month, department, category in [(3, 'clinic', 'doctor'), (9, 'surgery', 'nurse')]:
            for _ in range(5):
                self.response(department=department, category=category, submitted_on=date(2026, month, 10))
        saved = list(SurveyResponse.objects.order_by('id').values())
        self.client.force_login(self.admin)
        for half in (1, 2):
            with self.subTest(half=half):
                data = survey_analytics(self.campaign, 2026, half)
                self.assertEqual((data['count'], data['total']), (5, 50))
                self.assertEqual(data['groups'], [])
                page = self.client.get(reverse('survey_dashboard'), {'period': f'2026-{half}'})
                self.assertContains(page, 'средний общий балл')
                for removed in ('Категории сотрудников', 'Категория:', 'Средний медперсонал', 'Подразделения и категории', 'Подразделение:', 'Поликлиника', 'Хирургия'):
                    self.assertNotContains(page, removed)
                with patch('core.survey_pdf.Paragraph', wraps=Paragraph) as paragraphs:
                    pdf = self.client.get(reverse('survey_pdf', args=[self.campaign.pk]), {'period': f'2026-{half}'})
                self.assertEqual(pdf.status_code, 200)
                self.assertTrue(pdf.content.startswith(b'%PDF-'))
                text = '\n'.join(str(call.args[0]) for call in paragraphs.call_args_list)
                self.assertIn('Средний общий балл', text)
                for removed in ('Показатели по категориям', 'Категория:', 'Средний медперсонал', 'Подразделение:', 'Поликлиника', 'Хирургия'):
                    self.assertNotIn(removed, text)
        self.assertEqual(list(SurveyResponse.objects.order_by('id').values()), saved)

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

    def test_guest_entry_is_a_public_section_hub(self):
        page = self.client.get(reverse('login'))
        self.assertContains(page, 'href="/quality/guest/">Вход без логина</a>')
        self.assertNotContains(page, 'Вход без логина — анонимный опрос')
        hub = self.client.get(reverse('guest_home'))
        self.assertContains(hub, 'Профессиональное благополучие и выгорание')
        self.assertContains(hub, reverse('survey_public'))
        self.assertNotContains(hub, 'side-nav')
        self.client.force_login(self.employee)
        self.assertNotContains(self.client.get(reverse('guest_home')), self.employee.username)

    @patch('django.utils.timezone.localdate', return_value=date(2026, 11, 19))
    def test_analytics_and_pdf_use_the_selected_halfyear(self, today):
        for _ in range(5):
            self.response(submitted_on=date(2026, 6, 30))
        for _ in range(7):
            self.response(answers=[4] * 10, submitted_on=date(2026, 7, 1))
        first = survey_analytics(self.campaign, 2026, 1)
        second = survey_analytics(self.campaign, 2026, 2)
        self.assertEqual((first['count'], first['total']), (5, 50))
        self.assertEqual((second['count'], second['total']), (7, 40))
        self.assertEqual(second['date_to'], date(2026, 11, 19))
        self.client.force_login(self.admin)
        page = self.client.get(reverse('survey_dashboard'), {'period': '2026-1'})
        self.assertEqual(page.context['analytics']['count'], 5)
        self.assertContains(page, '?period=2026-1')
        self.assertEqual({p['value']: p['count'] for p in page.context['period_options']}, {'2026-1': 5, '2026-2': 7})
        with patch('core.survey_pdf.build_survey_pdf', return_value=b'%PDF-period-test') as builder:
            pdf = self.client.get(reverse('survey_pdf', args=[self.campaign.pk]), {'period': '2026-2'})
        self.assertEqual(builder.call_args.args[1]['count'], 7)
        self.assertIn('2026-H2.pdf', pdf['Content-Disposition'])

    def test_malformed_reporting_period_is_rejected(self):
        self.client.force_login(self.admin)
        for period in ('2026-3', 'not-a-date', '9999-2', '2026-1-2'):
            self.assertEqual(self.client.get(reverse('survey_dashboard'), {'period': period}).status_code, 400)
            self.assertEqual(self.client.get(reverse('survey_pdf', args=[self.campaign.pk]), {'period': period}).status_code, 400)

    @patch('django.utils.timezone.localdate', return_value=date(2026, 11, 19))
    def test_new_submission_updates_total_current_half_and_live_fragment(self, today):
        self.campaign.opens_on = date(2026, 1, 1)
        self.campaign.save(update_fields=['opens_on'])
        for _ in range(5):
            self.response(submitted_on=date(2026, 3, 10))
        self.client.force_login(self.admin)
        url = reverse('survey_dashboard')
        self.assertEqual(self.client.get(url).context['analytics']['count'], 0)
        respondent = Client()
        payload = self.submit_payload(respondent)
        self.assertRedirects(respondent.post(reverse('survey_public'), payload), reverse('survey_public'))
        page = self.client.get(url, {'year': 'auto', 'half': 'auto', 'refresh': '1'})
        self.assertEqual(page.context['analytics']['count'], 1)
        self.assertEqual(page.context['all_responses_count'], 6)
        self.assertEqual({p['half']: p['count'] for p in page.context['period_options']}, {1: 5, 2: 1})
        self.assertContains(page, 'id="survey-period-count">1</div>')
        self.assertContains(page, 'id="survey-total-count">6</div>')
        self.assertContains(page, 'year=auto&amp;half=auto')
        self.assertNotContains(page, '<!doctype')
        self.assertNotContains(page, 'Настройки приёма ответов')
        self.assertIn('no-store', page['Cache-Control'])
        self.assertEqual(self.client.get(url, {'year': 'auto', 'half': '1'}).context['analytics']['count'], 5)
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(url, {'refresh': '1'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url, {'refresh': '1'}).status_code, 302)

    def test_current_period_and_pdf_follow_year_changes_without_reconfiguration(self):
        self.response(submitted_on=date(2026, 12, 31))
        self.response(submitted_on=date(2027, 1, 1))
        self.client.force_login(self.admin)
        url = reverse('survey_dashboard')
        for day, selected in [(date(2026, 12, 31), '2026-2'), (date(2027, 1, 1), '2027-1'), (date(2027, 7, 1), '2027-2')]:
            with self.subTest(day=day), patch('django.utils.timezone.localdate', return_value=day):
                page = self.client.get(url, {'year': 'auto', 'half': 'auto'})
                self.assertEqual(page.context['selected_period'], selected)
                self.assertEqual(page.context['selected_year'], 'auto')
                self.assertEqual(page.context['analytics']['period_label'], f'{selected[-1]} полугодие')
                self.assertEqual(page.context['analytics']['count'], 0 if day.month == 7 else 1)
                with patch('core.survey_pdf.build_survey_pdf', return_value=b'%PDF-auto-test') as builder:
                    pdf = self.client.get(reverse('survey_pdf', args=[self.campaign.pk]), {'year': 'auto', 'half': 'auto'})
                self.assertEqual(builder.call_args.args[1]['period_value'], selected)
                self.assertIn(f'{day.year}-H{selected[-1]}.pdf', pdf['Content-Disposition'])
        with patch('django.utils.timezone.localdate', return_value=date(2027, 1, 1)):
            page = self.client.get(url, {'year': '2026', 'half': '2'})
            self.assertEqual(page.context['analytics']['count'], 1)
            self.assertEqual(page.context['year_options'], [2027, 2026])

    def test_browser_can_submit_once_each_halfyear_with_unlinked_receipts(self):
        self.campaign.opens_on = date(2025, 1, 1)
        self.campaign.save(update_fields=['opens_on'])
        dates = [date(2026, 6, 30), date(2026, 7, 1), date(2027, 1, 1)]
        for index, day in enumerate(dates, 1):
            with self.subTest(day=day), patch('django.utils.timezone.localdate', return_value=day):
                page = self.client.get(reverse('survey_public'))
                self.assertFalse(page.context['already_submitted'])
                payload = self.submit_payload(self.client)
                self.client.post(reverse('survey_public'), payload)
                self.assertTrue(self.client.get(reverse('survey_public')).context['already_submitted'])
                self.client.post(reverse('survey_public'), payload)
                self.assertEqual(SurveyResponse.objects.count(), index)
        self.assertEqual(set(SurveyResponse.objects.values_list('submitted_on', flat=True)), set(dates))
        self.assertEqual(len(set(SurveyResponse.objects.values_list('receipt', flat=True))), 3)

    def test_invalid_year_and_half_filters_are_rejected(self):
        self.client.force_login(self.admin)
        for filters in ({'year': 'no'}, {'half': '3'}, {'year': '9999'}, {'half': '2026-1'}):
            for name, args in [('survey_dashboard', []), ('survey_pdf', [self.campaign.pk])]:
                self.assertEqual(self.client.get(reverse(name, args=args), filters).status_code, 400)

    def test_date_distribution_preserves_answers_and_leaves_new_submissions_untouched(self):
        for i in range(114):
            SurveyResponse.objects.create(
                campaign=self.campaign,
                receipt=uuid.uuid5(uuid.NAMESPACE_URL, f'test-history:2026:20261119:survey:{i}'),
                answers=[4 + i % 2] * 10, submitted_on=date(2026, 11, 19),
                additional_answers={'11': '1'}, comment=f'Ответ {i}',
            )
        new_response = self.response(submitted_on=date(2026, 11, 19), comment='Новая анкета через форму')
        before = list(SurveyResponse.objects.order_by('id').values('id', 'receipt', 'answers', 'additional_answers', 'comment'))
        args = {'campaign': self.campaign.slug, 'as_of': date(2026, 11, 19), 'stdout': StringIO()}
        call_command('distribute_survey_halfyears', **args)
        self.assertEqual(self.campaign.responses.filter(submitted_on__lt=date(2026, 7, 1)).count(), 0)
        call_command('distribute_survey_halfyears', apply=True, **args)
        self.assertEqual(self.campaign.responses.filter(submitted_on__lt=date(2026, 7, 1)).count(), 77)
        self.assertEqual(self.campaign.responses.filter(submitted_on__gte=date(2026, 7, 1)).count(), 38)
        self.assertEqual(list(SurveyResponse.objects.order_by('id').values('id', 'receipt', 'answers', 'additional_answers', 'comment')), before)
        new_response.refresh_from_db()
        self.assertEqual(new_response.submitted_on, date(2026, 11, 19))
        self.assertTrue(all(day.weekday() < 5 for day in self.campaign.responses.values_list('submitted_on', flat=True)))
        dates = list(self.campaign.responses.order_by('id').values_list('submitted_on', flat=True))
        call_command('distribute_survey_halfyears', apply=True, **args)
        self.assertEqual(list(self.campaign.responses.order_by('id').values_list('submitted_on', flat=True)), dates)

    def test_distribution_cancels_if_original_set_is_incomplete(self):
        response = self.response(submitted_on=date(2026, 11, 19))
        with self.assertRaises(CommandError):
            call_command('distribute_survey_halfyears', campaign=self.campaign.slug, as_of=date(2026, 11, 19), apply=True, stdout=StringIO())
        response.refresh_from_db()
        self.assertEqual(response.submitted_on, date(2026, 11, 19))
