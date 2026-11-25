"""Generate an aggregate-only, vector-chart PDF with embedded Cyrillic font."""
from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_LOCK = Lock()


def register_font():
    with FONT_LOCK:
        if 'Cyamio' not in pdfmetrics.getRegisteredFontNames():
            candidates = [
                Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                Path('C:/Windows/Fonts/arial.ttf'),
            ]
            path = next((p for p in candidates if p.is_file()), None)
            if not path:
                raise RuntimeError('Cyrillic PDF font is missing. Install fonts-dejavu-core.')
            pdfmetrics.registerFont(TTFont('Cyamio', str(path)))


def bar_chart(rows, maximum=5):
    drawing = Drawing(485, 32 * len(rows) + 10)
    for i, (label, value) in enumerate(rows):
        y = drawing.height - (i + 1) * 32
        drawing.add(String(0, y + 8, label, fontName='Cyamio', fontSize=9, fillColor=colors.HexColor('#16334d')))
        drawing.add(Rect(190, y, 235, 22, fillColor=colors.HexColor('#e9f0f6'), strokeColor=None))
        drawing.add(Rect(190, y, min(value / maximum, 1) * 235, 22, fillColor=colors.HexColor('#1674b8'), strokeColor=None))
        drawing.add(String(435, y + 7, str(value), fontName='Cyamio', fontSize=9))
    return drawing


def build_survey_pdf(campaign, data):
    register_font()
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=42, rightMargin=42, topMargin=36, bottomMargin=42)
    styles = getSampleStyleSheet()
    for name in ('Normal', 'Title', 'Heading1', 'Heading2'):
        styles[name].fontName = 'Cyamio'
    styles['Normal'].fontSize = 9
    styles['Normal'].leading = 13
    styles['Title'].fontSize = 19
    styles['Title'].leading = 25
    styles['Heading2'].fontSize = 12
    styles['Heading2'].leading = 17
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8, leading=11)

    def p(value, style='Normal'):
        return Paragraph(escape(str(value)), styles[style])

    def table(rows, widths=None):
        wrapped = [[Paragraph(escape(str(value)), small) for value in row] for row in rows]
        result = Table(wrapped, colWidths=widths, repeatRows=1, hAlign='LEFT')
        result.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9f0f6')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEBELOW', (0, 0), (-1, -1), .4, colors.HexColor('#d9e6ef')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        return result

    logo = settings.BASE_DIR / 'core/static/core/cyamio-logo.png'
    story = []
    if logo.is_file():
        story.append(Image(str(logo), width=54, height=54, hAlign='LEFT'))
    story += [p('ЦЯМиО · Профессиональное благополучие', 'Title'), p(campaign.title, 'Heading2')]
    if data.get('period_label'):
        story += [p(data['period_label'], 'Heading2'), p(f'Период отчёта: {data["date_from"]:%d.%m.%Y} — {data["date_to"]:%d.%m.%Y}.')]
    story += [p(f'Сформировано: {timezone.localtime():%d.%m.%Y %H:%M}.'), p(f'Получено анкет: {data["count"]}. Численность аудитории: {campaign.audience_size or "не задана"}.'), p(f'Отношение количества анкет к численности аудитории: {str(data["response_ratio"]) + "%" if data["response_ratio"] is not None else "не рассчитано"}.'), p('Анкетирование добровольное и анонимное. Одна анкета не подтверждает личность сотрудника; число анкет не является проверенным числом уникальных сотрудников.'), Spacer(1, 14)]
    if not data['visible']:
        story += [p('Недостаточно ответов для публикации показателей', 'Heading2'), p(f'Показатели доступны после получения как минимум {data["minimum_group"]} анкет. Индивидуальные ответы не включаются в отчёт.')]
    else:
        story += [p('Основные показатели', 'Heading2'), table([
            ['Показатель', 'Значение', 'Интерпретация по рабочей методике'],
            ['Средний общий балл', f'{data["total"]} / 50', '40–50 — благоприятная рабочая среда'],
            ['Поддерживающая среда', f'{data["support"]} / 25', '20–25 — благоприятная поддерживающая среда'],
            ['Темп работы и документация', f'{data["pace"]} / 25', '20–25 — благоприятная оценка нагрузки'],
            ['Признаки выгорания', f'{data["burnout"]}%', 'Ответы 1–3 на вопрос 2'],
            ['Благоприятная оценка среды', f'{data["favourable"]}%', 'Доля анкет с суммой не менее 40'],
            ['Напряжение из-за МИС', f'{data["mis_stress"]}%', 'Ответы 1–2 на вопрос 9'],
        ], [160, 75, 270]), Spacer(1, 12), p('Распределение основных показателей, %', 'Heading2'), bar_chart([
            ('Благоприятная среда', data['favourable']), ('Признаки выгорания', data['burnout']), ('Напряжение из-за МИС', data['mis_stress']),
        ], 100), Spacer(1, 10), p('Средние баллы по основным вопросам', 'Heading2'), bar_chart([(f'Вопрос {q["number"]}', q['mean']) for q in data['questions']])]
        for question in data['questions']:
            story += [p(f'{question["number"]}. {question["label"]}', 'Heading2'), table([
                ['Вариант ответа', 'Анкет', '%'],
                *[[f'{a["value"]} — {a["label"]}', a['count'], a['percent']] for a in question['answers']],
            ], [380, 55, 70])]
        story.append(p('Дополнительные вопросы (в балл не входят)', 'Heading2'))
        for question in data['extra']:
            story.append(p(f'{question["number"]}. {question["label"]}', 'Heading2'))
            if question['visible']:
                story.append(table([['Ответ', 'Анкет', '%'], *[[a['label'], a['count'], a['percent']] for a in question['answers']]], [380, 55, 70]))
            else:
                story.append(p('Недостаточно ответов для публикации распределения.'))
        story.append(p('Темы предложений', 'Heading2'))
        story.append(p('Автоматическая группировка по ключевым словам; одно предложение может относиться к нескольким темам. Отображаются темы не менее чем из пяти анкет. Исходные комментарии не публикуются.'))
        story.extend(p(f'{theme["label"]}: {theme["count"]}') for theme in data['themes'])
        if not data['themes']:
            story.append(p('Тем с достаточным количеством упоминаний пока нет.'))
        if data.get('comments_included'):
            story += [PageBreak(), p('Обезличенные текстовые предложения', 'Heading2'),
                      p('Ответы на открытый вопрос 14. Доступ только сисадмину.'),
                      p(data['comments_notice']),
                      p(f'Текстовых ответов за выбранное полугодие: {data["comments_count"]}.'),
                      Spacer(1, 12)]
            for comment in data['comments']:
                story += [p(comment['text']), p(f'Повторений: {comment["count"]}'), Spacer(1, 12)]
            if not data['comments']:
                story.append(p('Текстовых предложений за выбранный период нет.'))
    story += [Spacer(1, 15), p('Методика расчёта', 'Heading2'), p('Источник: «Анкета Mini-Z — адаптация ЦЯМиО», рабочая версия 2026 года. Сумма вопросов 1–10: 10–50; вопросы 1–5 и 6–10: по 5–25. Баллы суммируются в исходном виде, без дополнительного обратного кодирования. Доля признаков выгорания: ответы 1–3 на вопрос 2 / число ответивших × 100%. Вопросы 11–14 не включаются в подсчёт. Результат не является медицинским диагнозом.'), p('Обобщённые показатели публикуются после получения не менее пяти анкет за выбранный период. В отчёте нет разбивки по сотрудникам, ФИО, логинов и индивидуальных анкет.')]

    def footer(canvas, document):
        canvas.setFont('Cyamio', 8)
        canvas.drawString(42, 22, 'ЦЯМиО · ' + data.get('period_label', 'Анонимный опрос · Обобщённые результаты'))
        canvas.drawRightString(A4[0] - 42, 22, str(document.page))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
