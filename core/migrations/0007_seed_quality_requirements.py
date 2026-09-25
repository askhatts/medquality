from django.db import migrations


REQUIREMENTS = [
    ('Культура безопасности', '5', 'Культура безопасности', 'Служба поддержки пациента', 'Зам. по мед. части (ККМУ)', 'https://app.notion.com/p/38500fe4a7b183c18b7581787a316ff5', ['Декларация культуры безопасности', 'Договор страхования профессиональной ответственности', 'СОП регистрации и оповещения об инцидентах', 'Реестр инцидентов', 'Приказ о назначении ответственных', 'План обучения по управлению инцидентами', 'Методика RCA']),
    ('Инфекционный контроль', '30, 31, 33, 59', 'Инфекционный контроль и гигиена рук', 'Служба инфекционного контроля', 'Зам. по мед. части (ККМУ)', 'https://app.notion.com/p/7db00fe4a7b1837c877801bd91872a52', ['Годовой план обучения', 'Презентации, видео и тесты', 'СОП вводного инструктажа', 'Журналы обучения', 'Отчёт комиссии инфекционного контроля', 'Проверка знаний и практических навыков']),
    ('Лекарственная безопасность', '50–52, 57', 'Безопасное обращение с лекарственными средствами', 'Отдел фармации', 'Зам. по мед. части (ККМУ)', 'https://app.notion.com/p/15b00fe4a7b18331964601b204a34d2d', ['Утверждённые СОП и приказы', 'Перечни high-alert, LASA и концентрированных электролитов', 'Правила маркировки и хранения', 'Журналы обучения', 'Мини-тесты и результаты', 'Индикаторы и квартальные отчёты PDCA']),
    ('Безопасность пациента', '55, 58, 60, 76, 77, 82', 'Идентификация и безопасность пациента', 'Главная медицинская сестра', 'Зам. по мед. части (ККМУ)', 'https://app.notion.com/p/da700fe4a7b1830ebccf8145c0041927', ['СОП идентификации пациента', 'Чек-листы с двумя идентификаторами', 'Журналы инструктажей', 'Проверка знаний', 'Наблюдательный аудит', 'Индикаторы безопасности и корректирующие меры']),
    ('Права пациента', '61–66', 'Права пациента и информированное согласие', 'Служба поддержки пациента', 'Зам. по мед. части (ККМУ)', '', ['Материалы инструктажа', 'Журналы обучения', 'Тесты и опросы', 'Формы информированного согласия', 'Аудит соблюдения прав', 'Корректирующие действия']),
    ('Безопасность персонала и ЧС', '13, 17, 22, 42, 45', 'Безопасность персонала, информация и чрезвычайные ситуации', 'Служба безопасности и охраны труда', 'Зам. по экономике и АХО', 'https://app.notion.com/p/6d600fe4a7b18394b332013df5e1b4d3', ['Годовые планы обучения', 'Журналы инструктажей', 'Приказы об ответственных', 'Протоколы пожарных учений', 'Допуски к оборудованию', 'Отчёты о выполнении планов']),
    ('Радиационная безопасность', '99', 'Работа с ИИИ и защита персонала', 'Служба радиационной безопасности и физической защиты', 'Зам. по ЯМ и стратегии', 'https://app.notion.com/p/34300fe4a7b1816ab153ff78f36ccc72', ['Санитарно-эпидемиологическое заключение', 'Журнал индивидуальной дозиметрии', 'Ежегодные замеры мощности дозы', 'Проверка защитной одежды', 'Первоначальное и ежегодное обучение', 'Журнал инструктажей и инструкции при авариях', 'Ежегодный отчёт комиссии']),
    ('Клинические компетенции', '83, 96, 98, 148, 151, 152, 155, 156, 159', 'Клинические компетенции и безопасная работа', 'Медицинские подразделения', 'Зам. по мед. части (ККМУ)', '', ['Сертификаты и документы повышения квалификации', 'Журналы стажировок', 'Допуски к медицинскому оборудованию', 'Тесты и симуляции', 'Протоколы обучения', 'Планы повышения квалификации']),
]


def seed(apps, schema_editor):
    Direction = apps.get_model('core', 'QualityDirection')
    Requirement = apps.get_model('core', 'QualityRequirement')
    Test = apps.get_model('core', 'Test')
    Assignment = apps.get_model('core', 'Assignment')
    Lesson = apps.get_model('core', 'Lesson')
    Document = apps.get_model('core', 'InternalDocument')
    for title, number, req_title, owner, curator, source, evidence in REQUIREMENTS:
        direction, _ = Direction.objects.get_or_create(title=title, defaults={'description': 'Обучение и доказательства непрерывного повышения качества медицинских услуг.'})
        Requirement.objects.update_or_create(direction=direction, number=number, defaults={'title': req_title, 'owner': owner, 'curator': curator, 'source_url': source, 'evidence': evidence})
    for test in Test.objects.order_by('course_id', 'id'):
        test.version = 1
        test.status = 'PUBLISHED'
        test.is_current = not Test.objects.filter(course_id=test.course_id, id__lt=test.id).exists()
        test.save(update_fields=['version', 'status', 'is_current'])
    for assignment in Assignment.objects.filter(test_version__isnull=True):
        assignment.test_version = Test.objects.filter(course_id=assignment.course_id, is_current=True).first()
        assignment.save(update_fields=['test_version'])
    for lesson in Lesson.objects.filter(category='INTERNAL').exclude(file=''):
        document, _ = Document.objects.get_or_create(title=lesson.title, file=lesson.file.name, defaults={'direction_id': lesson.course.direction_id})
        document.courses.add(lesson.course_id)


class Migration(migrations.Migration):
    dependencies = [('core', '0006_alter_question_options_alter_test_options_and_more')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
