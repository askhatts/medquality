from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Assignment, Course, Department, EmployeeType, InternalDocument, Lesson, Profile, QualityDirection, Question, Test, TestAttempt

class LearningPortalTests(TestCase):
    def setUp(self):
        dep = Department.objects.create(name='Тестовое отделение'); kind = EmployeeType.objects.create(name='Медицинский')
        self.employee = User.objects.create_user('employee', password='test-password'); Profile.objects.create(user=self.employee, role='EMPLOYEE', department=dep, employee_type=kind, position='Врач')
        methodist = User.objects.create_user('methodist', password='test-password'); Profile.objects.create(user=methodist, role='METHODIST', department=dep, employee_type=kind, position='Методист')
        direction = QualityDirection.objects.create(title='Безопасность пациента'); self.course = Course.objects.create(direction=direction, title='Тестовый курс')
        self.test = Test.objects.create(course=self.course, title='Итоговый тест', status='PUBLISHED'); Question.objects.create(test=self.test, text='Верный вариант?', options=['Да','Нет'], correct_index=0, correct_indexes=[0])
        self.assignment = Assignment.objects.create(user=self.employee, course=self.course, test_version=self.test, due_date=timezone.localdate()+timedelta(days=1), passing_score=80, attempts_allowed=1)
    def test_closed_dashboard(self): self.assertRedirects(self.client.get(reverse('dashboard')), '/quality/login/?next=/quality/')
    def test_department_assignment_creates_personal_assignment(self):
        self.client.login(username='methodist', password='test-password'); response = self.client.post(reverse('assignment_new'), {'course':self.course.id,'due_date':'2030-01-01','due_time':'22:00','passing_score':90,'attempts_allowed':2,'departments':[self.employee.profile.department_id]})
        self.assertEqual(response.status_code, 302); self.assignment.refresh_from_db(); self.assertEqual(self.assignment.passing_score, 90)
        self.assertEqual(self.assignment.due_time.strftime('%H:%M'), '22:00')
        self.assertEqual(self.assignment.attempts_allowed, 2)
    def test_successful_test_blocks_further_attempts(self):
        self.client.login(username='employee', password='test-password'); response = self.client.post(reverse('test', args=[self.course.id]), {f'q{self.test.questions.first().id}':'0'})
        self.assertContains(response, 'Тест пройден'); self.assignment.refresh_from_db(); self.assertEqual(self.assignment.status, 'COMPLETED'); self.assertRedirects(self.client.get(reverse('test', args=[self.course.id])), reverse('course', args=[self.course.id]))
    def test_reassignment_preserves_completed_history(self):
        self.assignment.status = 'COMPLETED'; self.assignment.completed_at = timezone.now(); self.assignment.save()
        self.client.login(username='methodist', password='test-password')
        self.client.post(reverse('assignment_new'), {'course':self.course.id,'due_date':'2030-01-01','passing_score':85,'attempts_allowed':2,'users':[self.employee.id]})
        cycles = Assignment.objects.filter(user=self.employee, course=self.course)
        self.assertEqual(cycles.count(), 2)
        self.assertEqual(cycles.filter(status='COMPLETED').count(), 1)
    def test_multiple_answer_question_is_scored(self):
        question = Question.objects.create(test=self.test, text='Выберите два', question_type='MULTIPLE', options=['A','B','C'], correct_indexes=[0,2], points=2, order=2)
        self.assignment.attempts_allowed = 2; self.assignment.save()
        self.client.login(username='employee', password='test-password')
        response = self.client.post(reverse('test', args=[self.course.id]), {f'q{self.test.questions.first().id}':'0', f'q{question.id}':['0','2']})
        self.assertContains(response, '100%')
    def test_edit_after_attempt_creates_new_test_version(self):
        TestAttempt.objects.create(assignment=self.assignment, number=1, correct_answers=0, score=0, passed=False)
        self.client.login(username='methodist', password='test-password')
        response = self.client.post(reverse('test_editor', args=[self.course.id]), {'action':'save_test','title':'Новая версия','instructions':''})
        self.assertEqual(response.status_code, 302)
        self.test.refresh_from_db(); self.assertFalse(self.test.is_current)
        current = Test.objects.get(course=self.course, is_current=True)
        self.assertEqual(current.version, 2); self.assertEqual(current.status, 'DRAFT')
    def test_document_bank_available_to_employee(self):
        self.client.login(username='employee', password='test-password')
        self.assertEqual(self.client.get(reverse('document_bank')).status_code, 200)

    def test_bank_document_can_be_linked_without_duplicate_legacy_lesson(self):
        legacy = Lesson.objects.create(course=self.course, title='Старая копия', category='INTERNAL', kind='DOC')
        document = InternalDocument.objects.create(
            title='Единый документ',
            file=SimpleUploadedFile('document.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
        )
        self.client.login(username='methodist', password='test-password')
        response = self.client.post(reverse('course_document_link', args=[self.course.id]), {'documents': [document.id]})
        self.assertRedirects(response, reverse('course_content', args=[self.course.id]))
        self.assertTrue(document.courses.filter(pk=self.course.pk).exists())
        response = self.client.get(reverse('course', args=[self.course.id]))
        self.assertContains(response, 'Единый документ')
        self.assertNotContains(response, legacy.title)

    def test_employee_cannot_change_course_document_links(self):
        document = InternalDocument.objects.create(
            title='Закрытый документ',
            file=SimpleUploadedFile('private.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
        )
        self.client.login(username='employee', password='test-password')
        response = self.client.post(reverse('course_document_link', args=[self.course.id]), {'documents': [document.id]})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(document.courses.filter(pk=self.course.pk).exists())

    def test_overdue_assignment_blocks_acknowledgement_and_test(self):
        self.assignment.due_date = timezone.localdate() - timedelta(days=1)
        self.assignment.save(update_fields=['due_date'])
        self.client.login(username='employee', password='test-password')
        response = self.client.post(reverse('acknowledge', args=[self.assignment.id]), follow=True)
        self.assertContains(response, 'Срок прохождения курса истёк')
        response = self.client.post(reverse('test', args=[self.course.id]), {f'q{self.test.questions.first().id}': '0'}, follow=True)
        self.assertContains(response, 'Срок теста истёк')
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, 'OVERDUE')
        self.assertIsNone(self.assignment.acknowledged_at)
        self.assertEqual(self.assignment.attempts.count(), 0)

    def test_methodist_can_create_and_edit_course(self):
        self.client.login(username='methodist', password='test-password')
        response = self.client.post(reverse('course_new'), {
            'category': 'Новая категория',
            'title': 'Новый учебный курс',
            'description': 'Краткое описание',
            'active': 'on',
        })
        created = Course.objects.get(title='Новый учебный курс')
        self.assertEqual(created.direction.title, 'Новая категория')
        self.assertRedirects(response, reverse('course_content', args=[created.id]))
        response = self.client.post(reverse('course_edit', args=[created.id]), {
            'category': 'Обновлённая категория',
            'title': 'Обновлённый курс',
            'description': 'Новое описание',
        })
        self.assertRedirects(response, reverse('course_content', args=[created.id]))
        created.refresh_from_db()
        self.assertEqual(created.title, 'Обновлённый курс')
        self.assertFalse(created.active)

    def test_pdf_can_open_inline_or_download(self):
        document = InternalDocument.objects.create(
            title='Документ PDF',
            file=SimpleUploadedFile('manual.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
        )
        document.courses.add(self.course)
        self.client.login(username='employee', password='test-password')
        page = self.client.get(reverse('course', args=[self.course.id]))
        self.assertContains(page, 'target="_blank"')
        self.assertContains(page, '?download=1')
        inline = self.client.get(document.file.url)
        download = self.client.get(document.file.url + '?download=1')
        self.assertIn('inline', inline.headers['Content-Disposition'])
        self.assertIn('attachment', download.headers['Content-Disposition'])

    def test_sidebar_uses_directions_instead_of_course_list(self):
        self.client.login(username='employee', password='test-password')
        response = self.client.get(reverse('directions'))
        self.assertContains(response, 'Направления качества')
        self.assertRedirects(self.client.get(reverse('courses')), reverse('directions'))

    def test_google_drive_material_is_embedded_and_opens_in_new_tab(self):
        lesson = Lesson.objects.create(
            course=self.course, title='Материал Drive', kind='VIDEO', order=1,
            url='https://drive.google.com/file/d/test-file_123/view',
        )
        self.client.login(username='employee', password='test-password')
        response = self.client.get(reverse('course', args=[self.course.id]))
        self.assertContains(response, lesson.drive_embed_url)
        self.assertContains(response, 'Открыть в Google Drive')
        self.assertContains(response, 'target="_blank"')

    def test_test_options_keep_original_values_when_display_order_is_shuffled(self):
        self.client.login(username='employee', password='test-password')
        response = self.client.get(reverse('test', args=[self.course.id]))
        self.assertContains(response, 'Порядок вариантов ответа меняется')
        self.assertContains(response, 'value="0"')
        self.assertContains(response, 'value="1"')

    def test_login_shows_welcome_identity_and_five_minute_session(self):
        self.employee.first_name = 'Иван'
        self.employee.last_name = 'Иванов'
        self.employee.save(update_fields=['first_name', 'last_name'])
        response = self.client.post(reverse('login'), {
            'username': 'employee', 'password': 'test-password',
        }, follow=True)
        self.assertContains(response, 'Добро пожаловать, Иван Иванов!')
        self.assertContains(response, 'Логин: employee')
        self.assertEqual(settings.SESSION_COOKIE_AGE, 300)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertEqual(self.client.get(reverse('session_ping')).status_code, 204)
