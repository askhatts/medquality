from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .assignment_queries import pending_course_assignments
from .models import Assignment, Course, Profile, QualityDirection, Test, TestAttempt
from .views import test_assignment as selected_test_assignment


class EmployeeAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('learner-status')
        cls.other = User.objects.create_user('other-learner-status')
        Profile.objects.create(user=cls.user)
        Profile.objects.create(user=cls.other)
        direction = QualityDirection.objects.create(title='Направление проверки назначений')
        cls.course = Course.objects.create(direction=direction, title='Проверяемый курс')
        cls.test = Test.objects.create(course=cls.course, title='Проверяемый тест', status='PUBLISHED')

    def setUp(self):
        self.client.force_login(self.user)

    def assign(self, **kwargs):
        values = dict(user=self.user, course=self.course, test_version=self.test,
                      due_date=timezone.localdate() + timedelta(days=1), attempts_allowed=2)
        values.update(kwargs)
        return Assignment.objects.create(**values)

    def page(self, tab='active'):
        return self.client.get(reverse('courses'), {'tab': tab})

    def test_expired_courses_are_separate_and_not_counted_in_bell_without_rewriting_status(self):
        assignment = self.assign(due_date=timezone.localdate() - timedelta(days=1))
        response = self.page()
        self.assertEqual(response.context['active_count'], 0)
        self.assertEqual(response.context['notification_count'], 0)
        self.assertEqual(response.context['unavailable_count'], 1)
        self.assertNotContains(response, self.course.title)
        unavailable = self.page('unavailable')
        self.assertContains(unavailable, self.course.title)
        self.assertContains(unavailable, 'Срок истёк')
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, 'ASSIGNED')
        self.assertFalse(assignment.attempts.exists())

    def test_deadline_includes_due_time_and_default_end_of_day(self):
        frozen = timezone.make_aware(datetime(2030, 11, 24, 22, 0))
        with patch('django.utils.timezone.now', return_value=frozen):
            assignment = self.assign(due_date=frozen.date(), due_time=time(21, 59))
            self.assertFalse(pending_course_assignments(self.user).exists())
            assignment.due_time = time(22, 0)
            assignment.save()
            self.assertTrue(pending_course_assignments(self.user).exists())
            assignment.due_time = None
            assignment.save()
            self.assertTrue(pending_course_assignments(self.user).exists())

    def test_failed_or_exhausted_test_does_not_appear_as_ready(self):
        assignment = self.assign(status='FAILED')
        response = self.page()
        self.assertEqual(response.context['notification_count'], 0)
        self.assertEqual(response.context['unavailable_count'], 1)
        assignment.status = 'IN_PROGRESS'
        assignment.save()
        for number in (1, 2):
            TestAttempt.objects.create(assignment=assignment, number=number, score=40, passed=False)
        response = self.page()
        self.assertEqual(response.context['active_count'], 0)
        self.assertContains(self.page('unavailable'), 'Попытки исчерпаны')
        self.assertEqual(self.client.post(reverse('test_start', args=[self.course.pk])).status_code, 302)
        self.assertEqual(assignment.attempts.count(), 2)

    def test_extended_overdue_record_can_be_taken_and_label_is_not_stale(self):
        assignment = self.assign(status='OVERDUE')
        response = self.page()
        self.assertEqual(response.context['active_count'], 1)
        self.assertEqual(response.context['notification_count'], 1)
        self.assertEqual(response.context['my_assignments'][0].display_status, 'Назначено')
        self.assertEqual(response.context['my_assignments'][0].pk, assignment.pk)

    def test_cancelled_latest_does_not_resurrect_old_active_assignment_on_course_or_test(self):
        old = self.assign()
        newest = self.assign(status='CANCELLED')
        response = self.client.get(reverse('course', args=[self.course.pk]))
        self.assertEqual(response.context['assignment'].pk, newest.pk)
        self.assertContains(response, 'Назначение отменено')
        self.assertNotContains(response, 'Я изучил(а) материалы')
        self.assertEqual(selected_test_assignment(self.course, self.user).pk, newest.pk)
        self.assertEqual(self.client.post(reverse('test_start', args=[self.course.pk])).status_code, 302)
        self.assertEqual(self.client.get(reverse('test', args=[self.course.pk])).status_code, 403)
        self.client.post(reverse('acknowledge', args=[old.pk]))
        old.refresh_from_db()
        self.assertIsNone(old.acknowledged_at)
        self.assertEqual(old.status, 'ASSIGNED')
        self.assertFalse(TestAttempt.objects.exists())

    def test_completed_latest_wins_over_older_active_and_other_users(self):
        self.assign()
        latest = self.assign(status='COMPLETED')
        self.assign(user=self.other)
        response = self.client.get(reverse('course', args=[self.course.pk]))
        self.assertEqual(response.context['assignment'].pk, latest.pk)
        self.assertContains(response, 'Курс успешно пройден')
        self.assertEqual(selected_test_assignment(self.course, self.user).pk, latest.pk)
        self.assertEqual(self.page().context['notification_count'], 0)

    def test_completed_link_opens_that_assignment_not_the_new_cycle(self):
        old = self.assign(status='COMPLETED', completed_at=timezone.now())
        TestAttempt.objects.create(assignment=old, number=1, score=90, passed=True)
        latest = self.assign()
        listing = self.page('completed')
        self.assertContains(listing, f'?assignment={old.pk}')
        response = self.client.get(reverse('course', args=[self.course.pk]), {'assignment': old.pk})
        self.assertEqual(response.context['assignment'].pk, old.pk)
        self.assertEqual(response.context['last_attempt'].score, 90)
        self.assertContains(response, 'Курс успешно пройден')
        self.assertNotContains(response, 'Я изучил(а) материалы')
        self.assertFalse(response.context['assignment_is_current'])
        self.assertEqual(selected_test_assignment(self.course, self.user).pk, latest.pk)

    def test_old_active_assignment_link_is_read_only(self):
        old = self.assign()
        self.assign()
        response = self.client.get(reverse('course', args=[self.course.pk]), {'assignment': old.pk})
        self.assertContains(response, 'Это предыдущее назначение')
        self.assertNotContains(response, 'Я изучил(а) материалы')
        self.client.post(reverse('acknowledge', args=[old.pk]))
        old.refresh_from_db()
        self.assertEqual(old.status, 'ASSIGNED')

    def test_assignment_parameter_cannot_show_another_persons_record_or_invalid_id(self):
        foreign = self.assign(user=self.other)
        url = reverse('course', args=[self.course.pk])
        self.assertEqual(self.client.get(url, {'assignment': foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {'assignment': 'bad'}).status_code, 404)

    def test_draft_or_missing_test_is_never_replaced_by_other_published_version(self):
        draft = Test.objects.create(course=self.course, title='Черновик', version=2, status='DRAFT')
        assignment = self.assign(test_version=draft)
        self.assertEqual(self.page().context['active_count'], 0)
        self.assertEqual(self.client.post(reverse('test_start', args=[self.course.pk])).status_code, 409)
        self.assertEqual(self.client.get(reverse('test', args=[self.course.pk])).status_code, 409)
        assignment.test_version = None
        assignment.save()
        self.assertEqual(self.client.post(reverse('test_start', args=[self.course.pk])).status_code, 409)
        self.assertNotContains(self.client.get(reverse('course', args=[self.course.pk])), 'Я изучил(а) материалы')
        assignment.refresh_from_db()
        self.assertIsNone(assignment.current_attempt_started_at)

    def test_ready_bell_uses_same_result_as_cards(self):
        assignment = self.assign()
        response = self.page()
        self.assertEqual(response.context['active_count'], response.context['notification_count'])
        self.assertEqual(response.context['active_count'], 1)
        self.assertContains(response, f'?assignment={assignment.pk}')
        self.course.active = False
        self.course.save()
        self.assertEqual(self.page().context['notification_count'], 0)
        self.assertEqual(self.client.post(reverse('test_start', args=[self.course.pk])).status_code, 302)

    def test_acknowledging_current_assignment_still_starts_learning(self):
        assignment = self.assign()
        response = self.client.post(reverse('acknowledge', args=[assignment.pk]))
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, 'IN_PROGRESS')
        self.assertIsNotNone(assignment.acknowledged_at)
        self.assertFalse(assignment.attempts.exists())
