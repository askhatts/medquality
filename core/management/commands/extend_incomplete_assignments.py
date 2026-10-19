"""Give every latest incomplete course cycle a common new deadline."""

from datetime import date, datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Assignment, AuditLog


class Command(BaseCommand):
    help = (
        'Продлевает открытые и просроченные назначения, а при исчерпанных '
        'попытках создаёт новый цикл, не удаляя историю.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--due-date', required=True, help='Новый срок в формате ГГГГ-ММ-ДД.')
        parser.add_argument('--due-time', default='23:59', help='Время окончания в формате ЧЧ:ММ.')
        parser.add_argument('--actor', help='Логин администратора для записи в журнал действий.')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            due_date = date.fromisoformat(options['due_date'])
            due_time = datetime.strptime(options['due_time'], '%H:%M').time()
        except ValueError as exc:
            raise CommandError('Используйте дату ГГГГ-ММ-ДД и время ЧЧ:ММ.') from exc

        actor = None
        if options.get('actor'):
            try:
                actor = User.objects.get(username=options['actor'])
            except User.DoesNotExist as exc:
                raise CommandError(f'Пользователь {options["actor"]} не найден.') from exc

        latest = {}
        queryset = Assignment.objects.select_related('course', 'test_version').prefetch_related(
            'attempts', 'course__tests',
        ).order_by('user_id', 'course_id', '-assigned_at', '-id')
        for assignment in queryset:
            latest.setdefault((assignment.user_id, assignment.course_id), assignment)

        extended = 0
        reopened = 0
        skipped = 0
        for assignment in latest.values():
            if assignment.status in ('COMPLETED', 'CANCELLED', 'REASSIGNED'):
                continue

            current_test = next((
                test for test in assignment.course.tests.all()
                if test.is_current and test.status == 'PUBLISHED'
            ), None)
            if not current_test:
                skipped += 1
                continue

            attempts_used = len(assignment.attempts.all())
            needs_new_cycle = (
                assignment.status == 'FAILED'
                or attempts_used >= assignment.attempts_allowed
                or assignment.test_version_id != current_test.id
            )
            if needs_new_cycle:
                Assignment.objects.filter(pk=assignment.pk).update(
                    status='REASSIGNED', current_attempt_started_at=None,
                )
                Assignment.objects.create(
                    user=assignment.user,
                    course=assignment.course,
                    test_version=current_test,
                    due_date=due_date,
                    due_time=due_time,
                    passing_score=assignment.passing_score,
                    attempts_allowed=assignment.attempts_allowed,
                    status='ASSIGNED',
                    is_demo=assignment.is_demo,
                )
                reopened += 1
                continue

            assignment.due_date = due_date
            assignment.due_time = due_time
            assignment.status = 'IN_PROGRESS' if attempts_used else 'ASSIGNED'
            assignment.current_attempt_started_at = None
            assignment.save(update_fields=[
                'due_date', 'due_time', 'status', 'current_attempt_started_at',
            ])
            extended += 1

        if actor:
            AuditLog.objects.create(
                user=actor,
                action='Массово продлены назначения',
                object_label=(
                    f'Срок: {due_date:%d.%m.%Y} {due_time:%H:%M}; '
                    f'продлено: {extended}; открыто заново: {reopened}; пропущено: {skipped}'
                ),
            )

        self.stdout.write(self.style.SUCCESS(
            f'Срок установлен до {due_date:%d.%m.%Y} {due_time:%H:%M}: '
            f'продлено {extended}, открыто заново {reopened}, пропущено {skipped}.'
        ))
