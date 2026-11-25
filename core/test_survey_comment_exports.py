import uuid
from datetime import date
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from reportlab.platypus import Paragraph

from .models import Profile, SurveyCampaign, SurveyResponse
from .survey_comments import mask_comment
from .surveys import survey_analytics


class SurveyCommentExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.campaign = SurveyCampaign.objects.create(slug='comments', title='Анкета', opens_on=date(2026, 1, 1))
        cls.admin = User.objects.create_user('comments-admin')
        Profile.objects.create(user=cls.admin, role='ADMIN')
        cls.users = []
        for role in ('EMPLOYEE', 'HEAD', 'METHODIST'):
            user = User.objects.create_user(f'comments-{role.lower()}')
            Profile.objects.create(user=user, role=role)
            cls.users.append(user)

    def response(self, text='', day=date(2026, 11, 20)):
        return SurveyResponse.objects.create(
            campaign=self.campaign, receipt=uuid.uuid4(), answers=[4] * 10,
            comment=text, submitted_on=day, department='clinic', category='doctor',
        )

    def seed(self):
        for _ in range(5):
            self.response('Просим упростить работу в МИС.')

    def test_mask_common_identifiers_without_changing_survey_answers(self):
        text = 'Иванов Иван Иванович, Иванова А.С., Петров А., +7 (777) 123-45-67, a@example.com, 123456789012, https://example.com/x'
        masked = mask_comment(text)
        for secret in ('Иванов', 'Иванова', 'Петров', '777', 'example.com', '123456789012'):
            self.assertNotIn(secret, masked)
        self.assertIn('[имя скрыто]', masked)
        self.assertIn('[номер скрыт]', masked)

    def test_comments_are_opt_in_grouped_unlinked_and_period_filtered(self):
        self.seed()
        self.response('Другое предложение', day=date(2026, 6, 20))
        before = list(SurveyResponse.objects.order_by('id').values())
        normal = survey_analytics(self.campaign, 2026, 2)
        self.assertEqual(normal['comments'], [])
        self.assertEqual(normal['comments_count'], 5)
        self.assertFalse(normal['comments_included'])
        included = survey_analytics(self.campaign, 2026, 2, include_comments=True)
        self.assertEqual(included['comments'], [{'text': 'Просим упростить работу в МИС.', 'count': 5}])
        self.assertEqual(set(included['comments'][0]), {'text', 'count'})
        self.assertEqual(list(SurveyResponse.objects.order_by('id').values()), before)

    def test_pdf_appendix_is_explicit_and_masked(self):
        self.seed()
        self.response('Иванов Иван Иванович просит написать на a@example.com')
        self.client.force_login(self.admin)
        url = reverse('survey_pdf', args=[self.campaign.pk])
        with patch('core.survey_pdf.Paragraph', wraps=Paragraph) as paragraphs:
            plain = self.client.get(url, {'year': '2026', 'half': '2'})
        plain_text = '\n'.join(str(call.args[0]) for call in paragraphs.call_args_list)
        self.assertEqual(plain.status_code, 200)
        self.assertNotIn('Просим упростить работу в МИС.', plain_text)
        with patch('core.survey_pdf.Paragraph', wraps=Paragraph) as paragraphs:
            report = self.client.get(url, {'year': '2026', 'half': '2', 'comments': '1'})
        text = '\n'.join(str(call.args[0]) for call in paragraphs.call_args_list)
        self.assertTrue(report.content.startswith(b'%PDF-'))
        self.assertIn('Обезличенные текстовые предложения', text)
        self.assertIn('Просим упростить работу в МИС.', text)
        self.assertNotIn('Иванов', text)
        self.assertNotIn('a@example.com', text)
        self.assertNotIn('clinic', text)
        self.assertNotIn('doctor', text)
        self.assertIn('no-store', report['Cache-Control'])
        self.assertIn('-suggestions.pdf', report['Content-Disposition'])

    def test_excel_is_only_text_and_frequency_and_neutralizes_formulas(self):
        self.seed()
        self.response('=DANGEROUS(1)')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('survey_comments_excel', args=[self.campaign.pk]), {'year': '2026', 'half': '2'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        book = load_workbook(BytesIO(response.content))
        sheet = book['Текстовые предложения']
        self.assertEqual(sheet.max_column, 2)
        self.assertEqual(sheet.max_row, 3)
        rows = list(sheet.values)
        self.assertEqual(rows[1], ("'=DANGEROUS(1)", 1))
        self.assertEqual(rows[2], ('Просим упростить работу в МИС.', 5))
        self.assertEqual(sheet['A2'].data_type, 's')
        self.assertNotIn('clinic', str(rows))
        self.assertNotIn('doctor', str(rows))
        self.assertEqual(sheet.freeze_panes, 'A2')

    def test_admin_dashboard_shows_export_buttons_but_no_comment_text(self):
        self.seed()
        self.client.force_login(self.admin)
        page = self.client.get(reverse('survey_dashboard'), {'year': '2026', 'half': '2'})
        self.assertContains(page, 'Открытые текстовые ответы')
        self.assertContains(page, 'Текстовые ответы Excel')
        self.assertContains(page, 'comments=1')
        self.assertNotContains(page, 'Просим упростить работу в МИС.')
        refreshed = self.client.get(reverse('survey_dashboard'), {'year': '2026', 'half': '2', 'refresh': '1'})
        self.assertContains(refreshed, 'Открытые текстовые ответы')
        self.assertNotContains(refreshed, 'Просим упростить работу в МИС.')

    def test_all_other_roles_and_anonymous_cannot_export_comments(self):
        self.seed()
        url = reverse('survey_comments_excel', args=[self.campaign.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        for user in self.users:
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            pdf = self.client.get(reverse('survey_pdf', args=[self.campaign.pk]), {'comments': '1'})
            self.assertEqual(pdf.status_code, 403)

    def test_small_period_never_exposes_text(self):
        self.response('Маленькая группа')
        self.client.force_login(self.admin)
        data = survey_analytics(self.campaign, 2026, 2, include_comments=True)
        self.assertEqual(data['comments'], [])
        self.assertFalse(data['visible'])
        response = self.client.get(reverse('survey_comments_excel', args=[self.campaign.pk]), {'year': '2026', 'half': '2'})
        book = load_workbook(BytesIO(response.content))
        self.assertEqual(book['Текстовые предложения'].max_row, 1)

    def test_bad_period_is_400_and_post_is_405(self):
        self.client.force_login(self.admin)
        url = reverse('survey_comments_excel', args=[self.campaign.pk])
        self.assertEqual(self.client.get(url, {'year': 'bad', 'half': '2'}).status_code, 400)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_long_comments_and_xml_are_safely_rendered_in_pdf(self):
        self.seed()
        self.response('<script>alert(1)</script> ' + 'предложение ' * 150)
        self.client.force_login(self.admin)
        result = self.client.get(reverse('survey_pdf', args=[self.campaign.pk]), {'year': '2026', 'half': '2', 'comments': '1'})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.content.startswith(b'%PDF-'))
