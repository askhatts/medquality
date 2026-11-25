"""Learning results for one assignment event, never anonymous survey data."""

from collections import Counter
from datetime import date, datetime
from io import BytesIO
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from .models import TestAttempt
from .views import deny, profile, report_entry, visible_assignments


STATES = {
    'ASSIGNED': 'Ожидают начала', 'IN_PROGRESS': 'В процессе',
    'COMPLETED': 'Пройдено', 'FAILED': 'Не пройдено',
    'OVERDUE': 'Просрочено', 'REASSIGNED': 'Переназначено',
}


def scoped_assignments(user):
    qs = visible_assignments(user).filter(user__is_active=True).exclude(status='CANCELLED')
    # A head without a department must not see everyone without a department.
    if profile(user).role == 'HEAD' and profile(user).department_id is None:
        return qs.none()
    return qs


def event_key(row):
    if row['batch_key']:
        return str(row['batch_key'])
    day = timezone.localtime(row['assigned_at']).date()
    return f"legacy:{row['course_id']}:{day.isoformat()}"


def summary(rows):
    counts = Counter(row['status'] for row in rows)
    scores = [row['score'] for row in rows if row['score'] is not None]
    return {
        'total': len(rows), 'counts': counts,
        'assigned': counts['ASSIGNED'], 'in_progress': counts['IN_PROGRESS'],
        'completed': counts['COMPLETED'], 'failed': counts['FAILED'],
        'overdue': counts['OVERDUE'], 'reassigned': counts['REASSIGNED'],
        'tested': len(scores),
        'average': round(sum(scores) / len(scores), 1) if scores else None,
        'rate': round(100 * counts['COMPLETED'] / len(rows), 1) if rows else 0,
    }


def question_results(rows):
    """Only the selected event's latest attempts need the larger answer JSON."""
    attempts = {
        row['last_attempt'].pk: row for row in rows if row['last_attempt'] and not row['recovered']
    }
    groups = {}
    for attempt in TestAttempt.objects.filter(pk__in=attempts).only('id', 'answers_snapshot'):
        row = attempts[attempt.pk]
        department = row['profile'].department
        version = row['assignment'].test_version
        for answer in attempt.answers_snapshot:
            if not isinstance(answer, dict) or not answer.get('question'):
                continue  # Old aggregates and expired-timer snapshots have no per-question data.
            key = (
                department.pk if department else None,
                version.pk if version else None,
                answer.get('question_id'), answer['question'],
            )
            group = groups.setdefault(key, {
                'department': str(department) if department else 'Без отделения',
                'version': version.version if version else None,
                'question': answer['question'], 'total': 0, 'correct': 0,
                'unanswered': 0,
            })
            group['total'] += 1
            group['correct'] += bool(answer.get('is_correct'))
            group['unanswered'] += not bool(answer.get('selected'))
    for group in groups.values():
        group['incorrect'] = group['total'] - group['correct']
        group['rate'] = round(100 * group['correct'] / group['total'], 1)
    return sorted(groups.values(), key=lambda item: (
        item['department'].casefold(), item['version'] or 0, item['question'],
    ))


def analytics_data(request):
    qs = scoped_assignments(request.user)
    # Lightweight metadata; do not materialize all historical answer snapshots.
    metadata = list(qs.order_by('-assigned_at', '-id').values(
        'id', 'batch_key', 'course_id', 'course__title', 'assigned_at',
    ))
    courses = sorted({(r['course_id'], r['course__title']) for r in metadata}, key=lambda r: r[1])
    course_id = request.GET.get('course', '')
    if course_id:
        try:
            course_id = int(course_id)
        except (ValueError, TypeError):
            raise Http404('Курс не найден.')
        if course_id not in {pk for pk, title in courses}:
            raise Http404('Курс не найден.')
        metadata = [r for r in metadata if r['course_id'] == course_id]
    events = {}
    for row in metadata:
        key = event_key(row)
        if key not in events:
            legacy = row['batch_key'] is None
            local = timezone.localtime(row['assigned_at'])
            label = (
                f'Архив за {local:%d.%m.%Y}' if legacy else
                f'Назначение №{row["id"]} от {local:%d.%m.%Y %H:%M}'
            )
            events[key] = {
                'key': key, 'title': row['course__title'], 'course_id': row['course_id'],
                'label': f'{label} · {row["course__title"]}', 'legacy': legacy,
            }
    selected_key = request.GET.get('batch', '') or next(iter(events), '')
    if selected_key and selected_key not in events:
        raise Http404('Назначение не найдено.')
    selected = events.get(selected_key)
    rows = []
    if selected:
        if selected['legacy']:
            _, course, day = selected_key.split(':')
            assignments = qs.filter(batch_key__isnull=True, course_id=int(course), assigned_at__date=date.fromisoformat(day))
        else:
            assignments = qs.filter(batch_key=UUID(selected_key))
        latest = {}
        for assignment in assignments.order_by('-assigned_at', '-id'):
            latest.setdefault(assignment.user_id, assignment)
        rows = [report_entry(assignment) for assignment in latest.values()]
        rows.sort(key=lambda r: (r['user'].get_full_name() or r['user'].username).casefold())
    departments = {}
    for row in rows:
        department = row['profile'].department
        key = department.pk if department else None
        departments.setdefault(key, {
            'name': str(department) if department else 'Без отделения', 'rows': [],
        })['rows'].append(row)
    department_rows = []
    for item in departments.values():
        department_rows.append({'name': item['name'], **summary(item['rows'])})
    department_rows.sort(key=lambda item: item['name'].casefold())
    return {
        'courses': courses, 'selected_course': course_id, 'events': list(events.values()),
        'selected': selected, 'rows': rows, 'stats': summary(rows),
        'departments': department_rows, 'questions': question_results(rows),
        'export_query': urlencode({'batch': selected_key}) if selected else '',
    }


@login_required
@never_cache
def assignment_analytics(request):
    if profile(request.user).role not in ('ADMIN', 'METHODIST', 'HEAD'):
        return deny(request.user)
    return render(request, 'core/assignment_analytics.html', analytics_data(request))


def excel_value(value):
    if isinstance(value, datetime):
        return timezone.localtime(value).replace(tzinfo=None)
    # Excel must never interpret employee names or question text as a formula.
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def add_sheet(book, title, headers, rows):
    sheet = book.create_sheet(title)
    sheet.append(headers)
    for row in rows:
        sheet.append([excel_value(value) for value in row])
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='153D54')
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(55, max(16, len(str(column[0].value)) + 3))
        for cell in column[1:]:
            if isinstance(cell.value, datetime):
                cell.number_format = 'dd.mm.yyyy hh:mm'
    return sheet


@login_required
@never_cache
def assignment_analytics_excel(request):
    if profile(request.user).role not in ('ADMIN', 'METHODIST', 'HEAD'):
        return deny(request.user)
    data = analytics_data(request)
    book = Workbook()
    book.remove(book.active)
    add_sheet(book, 'Параметры', ['Показатель', 'Значение'], [
        ('Назначение', data['selected']['label'] if data['selected'] else 'Нет данных'),
        ('Сформировано', timezone.now()),
        ('Отделение', 'Текущее подразделение сотрудника'),
        ('Оценка', 'Последняя сохранённая попытка либо подтверждённое администратором восстановление; источник указан на листе «Сотрудники»'),
        ('Средний балл', 'Сотрудники с зарегистрированным результатом, включая восстановления'),
        ('Восстановление', 'Дата внесения восстановления не является датой сдачи теста. Неизвестная дата сдачи оставлена пустой; ответы не выдумываются.'),
        ('Архив', 'Для старых записей без группы: последнее назначение сотруднику за выбранный день'),
    ])
    add_sheet(book, 'Отделения', [
        'Отделение', 'Назначено', 'Ожидают', 'В процессе', 'Пройдено', 'Не пройдено',
        'Просрочено', 'Переназначено', 'Сдали попытку', 'Средний балл, %', 'Завершение, %',
    ], ([d['name'], d['total'], d['assigned'], d['in_progress'], d['completed'],
         d['failed'], d['overdue'], d['reassigned'], d['tested'], d['average'], d['rate']]
        for d in data['departments']))
    add_sheet(book, 'Сотрудники', [
        'Сотрудник', 'Логин', 'Категория', 'Отделение', 'Курс', 'Версия теста',
        '№ назначения', 'Дата назначения', 'Срок', 'Статус', 'Последняя оценка, %',
        'Дата результата', 'Использовано попыток', 'Лимит попыток', 'Порог, %',
        'Источник результата', 'Дата внесения восстановления',
    ], ([
        r['user'].get_full_name() or r['user'].username, r['user'].username,
        str(r['profile'].employee_type or ''), str(r['profile'].department or ''),
        r['course'].title,
        r['assignment'].test_version.version if r['assignment'].test_version else None,
        r['assignment'].pk, r['assignment'].assigned_at, r['assignment'].deadline,
        r['status_label'], r['score'], r['result_at'], r['attempts_used'],
        r['assignment'].attempts_allowed, r['assignment'].passing_score,
        r['result_source'], r['recorded_at'],
    ] for r in data['rows']))
    add_sheet(book, 'Вопросы', [
        'Отделение', 'Версия теста', 'Вопрос', 'Ответов', 'Верно', 'Неверно',
        'Без ответа', 'Верно, %',
    ], ([q['department'], q['version'], q['question'], q['total'], q['correct'],
         q['incorrect'], q['unanswered'], q['rate']] for q in data['questions']))
    output = BytesIO()
    book.save(output)
    response = HttpResponse(output.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="assignment-analytics.xlsx"'
    return response
