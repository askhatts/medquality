"""Record administrator-confirmed lost results, without manufacturing attempts."""
from collections import Counter
from datetime import datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from core.models import Assignment, AuditLog, Course, ResultRecovery


def recovery_scope(course_ids, start, end):
    # A cancelled/replaced newest cycle must not resurrect an older cycle.
    latest = Assignment.objects.filter(
        user_id=OuterRef('user_id'), course_id=OuterRef('course_id'),
    ).order_by('-assigned_at', '-pk').values('pk')[:1]
    return Assignment.objects.filter(
        course_id__in=course_ids, assigned_at__gte=start, assigned_at__lt=end,
        pk=Subquery(latest), user__is_active=True, course__active=True,
        user__is_superuser=False, user__profile__role__in=('EMPLOYEE', 'HEAD', 'METHODIST'),
    ).exclude(status__in=('CANCELLED', 'REASSIGNED'))


class Command(BaseCommand):
    help = 'Предпросмотр / регистрация подтверждённых администратором утраченных результатов.'

    def add_arguments(self, parser):
        parser.add_argument('--courses', nargs='+', type=int, required=True)
        parser.add_argument('--month', required=True, help='Месяц исходного назначения: YYYY-MM')
        parser.add_argument('--score', type=int, required=True)
        parser.add_argument('--actor', required=True, help='Логин действующего сисадмина')
        parser.add_argument('--reason', required=True)
        parser.add_argument('--confirm-outage', action='store_true')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        score = options['score']
        reason = options['reason'].strip()
        if not 1 <= score <= 100 or not 10 <= len(reason) <= 500:
            raise CommandError('Оценка: 1–100; основание: 10–500 символов.')
        try:
            start = datetime.strptime(options['month'], '%Y-%m')
            if start.strftime('%Y-%m') != options['month']:
                raise ValueError
            end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
            start, end = (timezone.make_aware(d) for d in (start, end))
        except (ValueError, OverflowError):
            raise CommandError('Неверный месяц. Формат: YYYY-MM.')
        if start > timezone.now():
            raise CommandError('Нельзя восстанавливать будущие назначения.')
        actor = User.objects.filter(
            username=options['actor'], is_active=True, profile__role='ADMIN',
        ).first()
        if actor is None:
            raise CommandError('Нужен действующий сисадмин.')
        course_ids = sorted(set(options['courses']))
        if Course.objects.filter(pk__in=course_ids, active=True).count() != len(course_ids):
            raise CommandError('Все выбранные курсы должны существовать и быть действующими.')
        if options['apply'] and not options['confirm_outage']:
            raise CommandError('Для записи требуется --confirm-outage: подтверждение администратора об утраченном результате.')

        def checked_scope():
            qs = recovery_scope(course_ids, start, end)
            if not qs.exists():
                raise CommandError('В указанной области нет действующих назначений.')
            if qs.filter(passing_score__gt=score).exists():
                raise CommandError('Оценка ниже порога выбранного назначения; изменения не внесены.')
            if qs.filter(test_version__isnull=True).exists():
                raise CommandError('Обнаружено назначение без версии теста.')
            previous = ResultRecovery.objects.filter(assignment__in=qs)
            if previous.exclude(score=score, reason=reason).exists():
                raise CommandError('Уже есть восстановление с другой оценкой или основанием. Перезапись запрещена.')
            return qs

        qs = checked_scope()
        counts = Counter(qs.values_list('course_id', flat=True))
        for course_id in course_ids:
            self.stdout.write(f'Курс {course_id}: {counts[course_id]} назначений')
        if not options['apply']:
            already = ResultRecovery.objects.filter(assignment__in=qs).count()
            self.stdout.write(f'Предварительный просмотр: всего {sum(counts.values())}, уже восстановлено {already}. Записей не изменено.')
            return

        with transaction.atomic():
            # Serialize with course assignment creation, then lock affected rows.
            list(Course.objects.select_for_update().filter(pk__in=course_ids).order_by('pk'))
            ids = list(checked_scope().values_list('pk', flat=True))
            assignments = list(Assignment.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
            existing = set(ResultRecovery.objects.filter(assignment_id__in=ids).values_list('assignment_id', flat=True))
            new = [a for a in assignments if a.pk not in existing]
            ResultRecovery.objects.bulk_create([
                ResultRecovery(
                    assignment=a, score=score, reason=reason, recorded_by=actor,
                    previous_status=a.status, previous_completed_at=a.completed_at,
                    previous_attempt_started_at=a.current_attempt_started_at,
                ) for a in new
            ])
            # Never invent answers, attempt numbers or a test completion time.
            Assignment.objects.filter(pk__in=[a.pk for a in new]).update(
                status='COMPLETED', current_attempt_started_at=None,
            )
            if new:
                AuditLog.objects.create(
                    user=actor, action='Восстановлены результаты после сбоя',
                    object_label=f'Курсы {course_ids}; назначения {options["month"]}; {score}%; записей {len(new)}. Основание хранится в восстановлении.',
                )
        self.stdout.write(self.style.SUCCESS(
            f'Восстановлено {len(new)}, ранее восстановлено {len(existing)}. Сохранённые попытки не изменены.'
        ))
