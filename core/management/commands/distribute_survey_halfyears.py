"""Redistribute dates of the 114 existing test-installation questionnaires.

Receipt IDs select only the previously generated set. New submissions through
the public form, answers, and all training history remain unchanged.
"""
import json
import random
import uuid
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, SurveyCampaign, SurveyResponse


def working_days(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5]


class Command(BaseCommand):
    help = 'Распределяет даты 114 существующих анкет: 77 в I полугодии, 37 во II, без изменения ответов.'

    def add_arguments(self, parser):
        parser.add_argument('--campaign', default='staff-wellbeing-2026-11')
        parser.add_argument('--year', type=int, default=2026)
        parser.add_argument('--as-of', type=date.fromisoformat)
        parser.add_argument('--seed', type=int, default=20261119)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        year = options['year']
        if not 2000 <= year <= 2099:
            raise CommandError('Некорректный год.')
        as_of = options['as_of'] or timezone.localdate()
        if not date(year, 7, 1) <= as_of <= min(date(year, 12, 31), timezone.localdate()):
            raise CommandError('Дата окончания должна быть во II полугодии и не позднее сегодняшнего дня.')
        campaign = SurveyCampaign.objects.filter(slug=options['campaign']).first()
        if not campaign:
            raise CommandError('Анкета не найдена.')
        receipts = [uuid.uuid5(uuid.NAMESPACE_URL, f'test-history:{year}:{options["seed"]}:survey:{i}') for i in range(114)]
        queryset = SurveyResponse.objects.filter(campaign=campaign, receipt__in=receipts)
        if queryset.count() != 114:
            raise CommandError('Не найдены все 114 исходных анкет; данные не изменены.')
        self.stdout.write(json.dumps({'selected_responses': 114, 'first_half': 77, 'second_half': 37,
                                     'first_half_range': [f'{year}-01-01', f'{year}-06-30'],
                                     'second_half_range': [f'{year}-07-01', as_of.isoformat()]}, ensure_ascii=False))
        if not options['apply']:
            return
        first_days = working_days(date(year, 1, 1), date(year, 6, 30))
        second_days = working_days(date(year, 7, 1), as_of)
        rng = random.Random(options['seed'] + 202)
        with transaction.atomic():
            campaign = SurveyCampaign.objects.select_for_update().get(pk=campaign.pk)
            responses = {r.receipt: r for r in queryset.select_for_update()}
            if len(responses) != 114:
                raise CommandError('Состав анкет изменился; операция отменена.')
            for index, receipt in enumerate(receipts):
                responses[receipt].submitted_on = rng.choice(first_days if index < 77 else second_days)
            SurveyResponse.objects.bulk_update(list(responses.values()), ['submitted_on'], batch_size=114)
            campaign.title = 'Профессиональное благополучие сотрудников ЦЯМиО'
            campaign.opens_on = min(campaign.opens_on, date(year, 1, 1))
            campaign.save(update_fields=['title', 'opens_on'])
            AuditLog.objects.create(action='Обновлены периоды анкетирования', object_label=f'{year}: I полугодие — 77 анкет, II полугодие — 37 анкет; ответы сохранены')
        self.stdout.write('Готово: I полугодие — 77; II полугодие — 37. Ответы и история обучения сохранены.')
