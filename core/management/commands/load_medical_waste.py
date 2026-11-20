"""Import the supplied Drive teaching material and answer key, without copying files."""

from datetime import date, datetime, time
from uuid import NAMESPACE_URL, uuid5

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Assignment, AuditLog, Course, Lesson, Profile, QualityDirection, Question, Test


TITLE = 'Обращение с медицинскими отходами'
MATERIAL_URL = 'https://drive.google.com/file/d/1ihfPwQVYv1NLSoc3Hy3I3stchoUR7goP/view?usp=drivesdk'
# The source contains the answer key and must NOT be exposed as a learner lesson.
ANSWER_SOURCE_URL = 'https://docs.google.com/document/d/1DpTx8DJKbBYcAptDksBCqQ5kRMfLPJhjCxlzIGBsJIc/edit?usp=drivesdk'
QUESTIONS = [
    (
        'К какому классу относятся медицинские отходы, содержащие материалы и инструменты, загрязнённые кровью или биологическими жидкостями пациентов?',
        ['Класс А', 'Класс Б', 'Класс В', 'Класс Г'], 1,
        'По учебному материалу, это класс Б — эпидемиологически опасные отходы.',
    ),
    (
        'Какая цветовая маркировка используется для пакетов и ёмкостей при сборе медицинских отходов класса «В» (чрезвычайно эпидемиологически опасных)?',
        ['Чёрная', 'Жёлтая', 'Красная', 'Белая'], 2,
        'В учебном материале для класса В используется красная маркировка.',
    ),
    (
        'На какую максимальную долю объёма разрешается заполнять коробки для безопасного сбора и утилизации (КБСУ) по учебному материалу?',
        ['Не более чем на 1/3 объёма', 'Не более чем на 1/2 объёма', 'Не более чем на 2/3 объёма', 'Полностью до самого верха'], 2,
        'На слайде «Инвентарь для сбора» указано: не более 2/3 объёма.',
    ),
    (
        'К какому классу относятся токсикологически опасные отходы, включающие ртутьсодержащие приборы, цитостатики и просроченные лекарственные средства?',
        ['Класс Б', 'Класс В', 'Класс Г', 'Класс Д'], 2,
        'Это класс Г — токсикологически опасные отходы.',
    ),
    (
        'Каков предельный срок хранения медицинских отходов классов «А» и «Б» непосредственно по месту их образования по учебному материалу?',
        ['Не более 12 часов', 'Не более одних суток', 'Не более трёх суток', 'Не более пяти суток'], 1,
        'Слайд «Жизненный цикл и сроки временного хранения» указывает не более одних суток по месту образования.',
    ),
    (
        'Что из перечисленного категорически запрещено делать при обращении с медицинскими отходами?',
        ['Маркировать ёмкости дата-стикерами', 'Утрамбовывать отходы руками', 'Использовать средства индивидуальной защиты (перчатки, маски)', 'Взвешивать отходы при сдаче в комнату временного хранения'], 1,
        'Утрамбовывание отходов руками запрещено: это создаёт риск травмирования и инфицирования.',
    ),
    (
        'Сколько времени допускается хранить отходы в помещении для временного хранения (или на специальных площадках) до вывоза специализированной организацией по учебному материалу?',
        ['Не более 24 часов', 'Не более 48 часов', 'Не более 3 суток (72 часа)', 'До 10 дней'], 2,
        'В предоставленном учебном материале указан срок не более трёх суток (72 часа).',
    ),
    (
        'Кто из медицинского персонала непосредственно осуществляет сбор отходов классов «Б» и «В» в структурных подразделениях?',
        ['Младший медицинский персонал', 'Средний медицинский персонал', 'Уборщицы территории', 'Заведующий отделением'], 1,
        'По слайду о зонах ответственности сбор осуществляет средний медицинский персонал, транспортировку — младший.',
    ),
    (
        'Какая маркировка наносится на ёмкости и пакеты при сборе радиоактивных медицинских отходов класса «Д»?',
        ['Белая окраска', 'Чёрная окраска', 'Знак радиоактивной опасности', 'Жёлтая окраска'], 2,
        'Используется знак радиоактивной опасности.',
    ),
    (
        'С какой периодичностью должна проводиться дезинфекция контейнеров для сбора отходов классов «А», «Б» и «В», а также спецавтотранспорта по учебному материалу?',
        ['Каждый день', '1 раз в 3 дня', '1 раз в неделю и по мере загрязнения', '1 раз в месяц'], 2,
        'В предоставленном ключе ответов указано: один раз в неделю и по мере загрязнения.',
    ),
]


def audience():
    return Profile.objects.filter(user__is_active=True, user__is_superuser=False).exclude(role='ADMIN')


class Command(BaseCommand):
    help = 'Курс «Обращение с медицинскими отходами»: предварительный просмотр; --apply для сохранения.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--assign', action='store_true')
        parser.add_argument('--due-date', default='2026-11-24')
        parser.add_argument('--due-time', default='23:59')
        parser.add_argument('--actor', default='sysadmin')

    def handle(self, *args, **options):
        try:
            due = date.fromisoformat(options['due_date'])
            due_time = time.fromisoformat(options['due_time'])
            if due_time.tzinfo is not None:
                raise ValueError
        except (ValueError, TypeError):
            raise CommandError('Укажите срок YYYY-MM-DD и время HH:MM.')
        if options['assign'] and timezone.make_aware(datetime.combine(due, due_time)) <= timezone.now():
            raise CommandError('Срок назначения должен быть в будущем.')
        user_ids = list(audience().order_by('user_id').values_list('user_id', flat=True))
        batch = uuid5(NAMESPACE_URL, f'{MATERIAL_URL}#assignment:{due.isoformat()}T{due_time.isoformat()}')
        self.stdout.write(f'Курс: {TITLE}; вопросов: {len(QUESTIONS)}; сотрудников: {len(user_ids)}')
        self.stdout.write(f'Срок: {due} {due_time:%H:%M}; порог: 80%; попыток: 2; таймер: 10 минут')
        self.stdout.write(f'Группа назначения: {batch}')
        if not options['apply']:
            self.stdout.write('Предварительный просмотр. Изменений нет. Для сохранения: --apply; для назначения: --assign.')
            return
        actor = User.objects.filter(username=options['actor'], profile__role='ADMIN', is_active=True).first()
        if not actor:
            raise CommandError('Не найдена активная учётная запись администратора --actor.')
        with transaction.atomic():
            course = Course.objects.filter(title=TITLE).first()
            if course is None:
                # SQLite's LIKE is not case-insensitive for Cyrillic; keep imports portable.
                direction = next((d for d in QualityDirection.objects.filter(active=True).order_by('pk') if 'инфекц' in d.title.casefold()), None)
                if direction is None:
                    direction, _ = QualityDirection.objects.get_or_create(title='Инфекционная безопасность', defaults={'active': True})
                course = Course.objects.create(
                    direction=direction, title=TITLE,
                    description='Классификация медицинских отходов, цветовая маркировка, безопасный сбор, временное хранение и зоны ответственности персонала.',
                )
            course = Course.objects.select_for_update().get(pk=course.pk)
            lesson = Lesson.objects.filter(course=course, url=MATERIAL_URL).first()
            if lesson is None:
                Lesson.objects.create(course=course, title='Система обращения с медицинскими отходами — учебная презентация', kind='DOC', category='LESSON', order=1, url=MATERIAL_URL)
            test = course.tests.filter(is_current=True, status='PUBLISHED').first()
            expected = [(text, choices, correct, [correct], explanation, i, 1, 'SINGLE') for i, (text, choices, correct, explanation) in enumerate(QUESTIONS, 1)]
            actual = list(test.questions.order_by('order', 'id').values_list(
                'text', 'options', 'correct_index', 'correct_indexes', 'explanation', 'order', 'points', 'question_type',
            )) if test else []
            if actual != expected or (test and test.time_limit_minutes != 10):
                version = (course.tests.order_by('-version').values_list('version', flat=True).first() or 0) + 1
                course.tests.filter(is_current=True).update(is_current=False)
                test = Test.objects.create(
                    course=course, title=f'Итоговый тест: {TITLE}', status='PUBLISHED',
                    version=version, is_current=True, time_limit_minutes=10,
                    instructions='10 вопросов по учебной презентации. Выберите один ответ в каждом вопросе и отправьте результат до окончания таймера.',
                )
                Question.objects.bulk_create([
                    Question(test=test, text=text, options=choices, correct_index=correct,
                             correct_indexes=indexes, explanation=explanation, order=order,
                             points=points, question_type=kind)
                    for text, choices, correct, indexes, explanation, order, points, kind in expected
                ])
            created = 0
            if options['assign']:
                existing = set(Assignment.objects.filter(batch_key=batch, course=course).values_list('user_id', flat=True))
                missing = sorted(set(user_ids) - existing)
                Assignment.objects.filter(course=course, user_id__in=missing, status__in=['ASSIGNED', 'IN_PROGRESS', 'OVERDUE']).update(status='REASSIGNED')
                Assignment.objects.bulk_create([
                    Assignment(user_id=user_id, course=course, test_version=test, batch_key=batch,
                               due_date=due, due_time=due_time, passing_score=80, attempts_allowed=2)
                    for user_id in missing
                ])
                created = len(missing)
            AuditLog.objects.create(user=actor, action='Добавлен курс по медицинским отходам', object_label=f'{course.title}; назначено: {created}')
        self.stdout.write(self.style.SUCCESS(f'Курс №{course.pk}, тест v{test.version}, новых назначений: {created}. Повторный запуск не дублирует назначения.'))
