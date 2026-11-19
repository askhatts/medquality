"""Questionnaire and scoring from the supplied Cyamio Mini-Z adaptation.

This is a local working adaptation, not a clinical diagnosis. All ten scales
already point towards a favourable score of five: never reverse-code again.
"""
from collections import Counter
from datetime import date
from statistics import mean

from django import forms
from django.utils import timezone

from .models import SurveyCampaign

MIN_GROUP = 5
AGREEMENT = ['Категорически не согласен(на)', 'Не согласен(на)', 'Нейтрально / затрудняюсь ответить', 'Согласен(на)', 'Полностью согласен(на)']
QUESTIONS = [
    ('В целом я удовлетворён(а) своей нынешней работой.', AGREEMENT),
    ('Какое утверждение наиболее точно описывает Ваше текущее состояние в отношении профессионального выгорания?', [
        'Чувствую выраженное эмоциональное истощение; считаю, что мне может потребоваться профессиональная помощь.',
        'Признаки выгорания сохраняются; часто испытываю неудовлетворённость и усталость от работы.',
        'Замечаю начальные признаки выгорания, например эмоциональное истощение.',
        'Работа вызывает стресс, иногда энергии меньше, чем раньше, но выгоревшим(ей) себя не считаю.',
        'Мне нравится моя работа; признаков выгорания не ощущаю.',
    ]),
    ('Мои профессиональные ценности и принципы согласуются с ценностями и подходами руководства ЦЯМиО.', AGREEMENT),
    ('Насколько эффективно взаимодействуют сотрудники Вашего подразделения при выполнении профессиональных обязанностей?', ['Неудовлетворительно', 'Скорее неудовлетворительно', 'Удовлетворительно', 'Хорошо', 'Очень хорошо']),
    ('Насколько Вы контролируете свою рабочую нагрузку?', ['Практически не контролирую', 'В незначительной степени', 'Частично', 'В значительной степени', 'Полностью контролирую']),
    ('Я испытываю значительный стресс из-за своей работы.', ['Полностью согласен(на)', 'Согласен(на)', 'Нейтрально / затрудняюсь ответить', 'Не согласен(на)', 'Категорически не согласен(на)']),
    ('Достаточно ли у Вас времени для оформления медицинской документации и выполнения административной работы?', ['Совершенно недостаточно', 'Недостаточно', 'В целом достаточно', 'Достаточно', 'Более чем достаточно']),
    ('Как много времени Вы тратите на медицинскую документацию вне рабочего времени (дома или после окончания смены)?', ['Очень много', 'Достаточно много', 'Умеренно', 'Немного', 'Практически не трачу']),
    ('Работа с медицинскими информационными системами и электронной документацией увеличивает моё профессиональное напряжение.', ['Полностью согласен(на)', 'Согласен(на)', 'Нейтрально / затрудняюсь ответить', 'Не согласен(на)', 'Категорически не согласен(на)']),
    ('Как бы Вы охарактеризовали общую рабочую атмосферу в Вашем основном подразделении?', ['Хаотичная, чрезмерно напряжённая', 'Очень напряжённая', 'Высокая нагрузка, но в целом управляемая', 'Достаточно спокойная', 'Спокойная и хорошо организованная']),
]
DEPARTMENTS = [
    ('clinic', 'Поликлиника'), ('chemo', 'Химиотерапия'), ('surgery', 'Хирургия'),
    ('radiotherapy', 'Лучевая терапия'), ('nuclear', 'Радионуклидная диагностика/терапия'),
    ('diagnostics', 'Диагностика'), ('other', 'Другое'),
]
CATEGORIES = [('doctor', 'Врач'), ('nurse', 'Средний медперсонал'), ('junior', 'Младший медперсонал'), ('other', 'Административный/иной персонал')]
EXTRA = [
    ('Достаточно ли кадровых ресурсов в Вашем подразделении для выполнения текущего объёма работы?', ['Да', 'Частично', 'Нет', 'Затрудняюсь ответить']),
    ('Получаете ли Вы необходимую поддержку непосредственного руководителя при возникновении сложных профессиональных ситуаций?', ['Всегда', 'Часто', 'Иногда', 'Редко', 'Никогда']),
    ('Испытываете ли Вы эмоциональное напряжение после взаимодействия с пациентами и их родственниками?', ['Никогда', 'Редко', 'Иногда', 'Часто', 'Очень часто']),
]
THEMES = [
    ('МИС и электронная документация', ('мис', 'информационн', 'электронн', 'dmed', 'дмед')),
    ('Кадровая обеспеченность', ('кадр', 'нехват', 'персонал', 'сотрудник')),
    ('Нагрузка и рабочее время', ('нагруз', 'переработ', 'смен', 'дежур', 'врем')),
    ('Поддержка и взаимодействие', ('руковод', 'коллег', 'команд', 'поддерж')),
]


class WellbeingForm(forms.Form):
    department = forms.ChoiceField(label='Подразделение', choices=[('', 'Не указывать')] + DEPARTMENTS, required=False)
    category = forms.ChoiceField(label='Категория персонала', choices=[('', 'Не указывать')] + CATEGORIES, required=False)
    tenure = forms.ChoiceField(label='Стаж работы в ЦЯМиО', required=False, choices=[('', 'Не указывать'), ('under1', 'До 1 года'), ('1to5', '1–5 лет'), ('6to10', '6–10 лет'), ('over10', 'Более 10 лет')])
    schedule = forms.ChoiceField(label='Режим работы', required=False, choices=[('', 'Не указывать'), ('day', 'Дневной'), ('shift', 'Сменный'), ('night', 'Есть ночные дежурства')])
    combined_roles = forms.ChoiceField(label='Совмещение должностей', required=False, choices=[('', 'Не указывать'), ('yes', 'Да'), ('no', 'Нет')])
    comment = forms.CharField(
        label='14. Какие факторы чаще всего вызывают у Вас профессиональное напряжение? Какие изменения в организации работы ЦЯМиО могли бы улучшить условия труда?',
        required=False, max_length=2000, widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Не указывайте ФИО, контакты и другие сведения, позволяющие установить личность.',
    )
    consent = forms.BooleanField(label='Я добровольно участвую в анонимном опросе и согласен(на) с обработкой ответов в обобщённом виде.')
    receipt = forms.CharField(widget=forms.HiddenInput)
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for number, (label, options) in enumerate(QUESTIONS, 1):
            self.fields[f'q{number}'] = forms.TypedChoiceField(
                label=f'{number}. {label}', choices=list(enumerate(options, 1)),
                coerce=int, widget=forms.RadioSelect,
            )
        for number, (label, options) in enumerate(EXTRA, 11):
            self.fields[f'q{number}'] = forms.ChoiceField(
                label=f'{number}. {label}', choices=[(str(i), option) for i, option in enumerate(options, 1)],
                required=False, widget=forms.RadioSelect,
            )

    @property
    def demographic_fields(self):
        return [self[name] for name in ('department', 'category', 'tenure', 'schedule', 'combined_roles')]

    @property
    def main_fields(self):
        return [self[f'q{i}'] for i in range(1, 11)]

    @property
    def extra_fields(self):
        return [self[f'q{i}'] for i in range(11, 14)]


class CampaignSettingsForm(forms.ModelForm):
    class Meta:
        model = SurveyCampaign
        fields = ['title', 'active', 'opens_on', 'closes_on', 'audience_size']
        labels = {'title': 'Название опроса', 'active': 'Приём ответов открыт', 'opens_on': 'Дата начала', 'closes_on': 'Дата окончания (необязательно)', 'audience_size': 'Численность аудитории для расчёта отклика'}
        widgets = {name: forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d') for name in ('opens_on', 'closes_on')}


def burnout_self_assessment(answers):
    """Describe the respondent's Q2 choice, not a diagnosis or total-score tier."""
    if not isinstance(answers, (list, tuple)) or len(answers) != 10:
        return None
    value = answers[1]
    if type(value) is not int or not 1 <= value <= 5:
        return None
    labels = {
        1: 'Выраженное эмоциональное истощение по самооценке',
        2: 'Сохраняющиеся признаки выгорания по самооценке',
        3: 'Начальные признаки выгорания по самооценке',
        4: 'Стресс без ощущения выгорания по самооценке',
        5: 'Признаки выгорания не отмечены по самооценке',
    }
    return {'label': labels[value], 'selected_answer': QUESTIONS[1][1][value - 1]}


def summarize(responses):
    count = len(responses)
    if count < MIN_GROUP:
        return {'count': count, 'visible': False}
    totals = [sum(r.answers) for r in responses]
    supports = [sum(r.answers[:5]) for r in responses]
    paces = [sum(r.answers[5:]) for r in responses]
    return {
        'count': count, 'visible': True,
        'total': round(mean(totals), 1), 'support': round(mean(supports), 1),
        'pace': round(mean(paces), 1),
        'favourable': round(sum(score >= 40 for score in totals) * 100 / count, 1),
        'burnout': round(sum(r.answers[1] <= 3 for r in responses) * 100 / count, 1),
        'mis_stress': round(sum(r.answers[8] <= 2 for r in responses) * 100 / count, 1),
    }


def halfyear_bounds(year, half):
    if not 2000 <= year <= 2099 or half not in (1, 2):
        raise ValueError('Некорректное полугодие.')
    return date(year, 1 if half == 1 else 7, 1), date(year, 6 if half == 1 else 12, 30 if half == 1 else 31)


def halfyear_options(campaign, selected_year=None):
    today = timezone.localdate()
    counts = Counter((day.year, 1 if day.month <= 6 else 2)
                     for day in campaign.responses.values_list('submitted_on', flat=True))
    years = {today.year} | {year for year, half in counts}
    if selected_year is not None:
        years.add(selected_year)
    return [
        {'value': f'{year}-{half}', 'year': year, 'half': half,
         'label': f'{half} полугодие',
         'count': counts[(year, half)]}
        for year in sorted(years, reverse=True) for half in (2, 1)
    ]


def selected_halfyear(value=None, *, year=None, half=None):
    if year is not None or half is not None:
        today = timezone.localdate()
        try:
            year = today.year if year in (None, '', 'auto') else int(year)
            half = (1 if today.month <= 6 else 2) if half in (None, '', 'auto') else int(half)
            halfyear_bounds(year, half)
        except (ValueError, TypeError):
            raise ValueError('Выберите корректный год и полугодие.')
        return year, half
    if not value:
        today = timezone.localdate()
        return today.year, 1 if today.month <= 6 else 2
    try:
        year, half = (int(item) for item in value.split('-'))
        halfyear_bounds(year, half)
    except (ValueError, TypeError):
        raise ValueError('Выберите полугодие в формате ГГГГ-1 или ГГГГ-2.')
    return year, half


def survey_analytics(campaign, year=None, half=None):
    queryset = campaign.responses.all()
    period = {}
    if year is not None and half is not None:
        start, end = halfyear_bounds(year, half)
        if start <= timezone.localdate():
            end = min(end, timezone.localdate())
        queryset = queryset.filter(submitted_on__gte=start, submitted_on__lte=end)
        period = {'period_value': f'{year}-{half}',
                  'period_label': f'{half} полугодие',
                  'date_from': start, 'date_to': end}
    responses = list(queryset)
    data = summarize(responses)
    data.update(period)
    data['response_ratio'] = round(len(responses) * 100 / campaign.audience_size, 1) if campaign.audience_size else None
    data['minimum_group'] = MIN_GROUP
    data['groups'] = []
    # Report only the whole selected period, never demographic breakdowns.
    # Preserve existing answers without exposing or inferring staff groups.
    data['questions'] = []
    data['extra'] = []
    data['themes'] = []
    if not data['visible']:
        return data
    for i, (label, options) in enumerate(QUESTIONS):
        counts = Counter(r.answers[i] for r in responses)
        data['questions'].append({
            'number': i + 1, 'label': label,
            'mean': round(mean(r.answers[i] for r in responses), 2),
            'width': round(mean(r.answers[i] for r in responses) * 20, 1),
            'answers': [{'label': option, 'value': value, 'count': counts[value], 'percent': round(counts[value] * 100 / len(responses), 1)} for value, option in enumerate(options, 1)],
        })
    for i, (label, options) in enumerate(EXTRA, 11):
        counts = Counter(r.additional_answers.get(str(i)) for r in responses)
        answered = sum(counts[str(j)] for j in range(1, len(options) + 1))
        data['extra'].append({
            'number': i, 'label': label, 'answered': answered,
            'visible': answered >= MIN_GROUP,
            'answers': [{'label': option, 'count': counts[str(j)], 'percent': round(counts[str(j)] * 100 / answered, 1) if answered else 0} for j, option in enumerate(options, 1)] if answered >= MIN_GROUP else [],
        })
    for label, keywords in THEMES:
        count = sum(any(word in r.comment.casefold() for word in keywords) for r in responses)
        if count >= MIN_GROUP:
            data['themes'].append({'label': label, 'count': count})
    return data
