import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .models import AuditLog, SurveyCampaign, SurveyResponse
from .surveys import CampaignSettingsForm, WellbeingForm, burnout_self_assessment, halfyear_bounds, halfyear_options, selected_halfyear, survey_analytics


def admin_only(user):
    return user.is_active and user.is_authenticated and getattr(getattr(user, 'profile', None), 'role', None) == 'ADMIN'


def receipt_cookie(campaign):
    return f'wellbeing_{campaign.pk}'


@never_cache
@require_http_methods(['GET'])
def guest_home(request):
    return render(request, 'core/guest_home.html')


@never_cache
@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters()
def survey_public(request):
    today = timezone.localdate()
    campaign = SurveyCampaign.objects.filter(
        active=True, opens_on__lte=today,
    ).filter(
        Q(closes_on__isnull=True) | Q(closes_on__gte=today)
    ).first()
    if not campaign:
        return render(request, 'core/survey_public.html', {'campaign': None})
    salt = f'wellbeing-receipt-{campaign.pk}'
    cookie = receipt_cookie(campaign)
    try:
        receipt = uuid.UUID(request.get_signed_cookie(cookie, salt=salt))
    except (KeyError, ValueError, signing.BadSignature):
        receipt = uuid.uuid4()
    start, end = halfyear_bounds(today.year, 1 if today.month <= 6 else 2)
    previous = SurveyResponse.objects.filter(campaign=campaign, receipt=receipt).only('submitted_on', 'answers').first()
    # A browser may answer again in a new half-year, with a fresh random
    # receipt. Past and future periods are never linked through one receipt.
    already_submitted = previous is not None and start <= previous.submitted_on <= end
    if previous is not None and not already_submitted:
        receipt = uuid.uuid4()
    hidden_cookie = f'wellbeing_result_hidden_{campaign.pk}'
    hidden_salt = f'wellbeing-result-hidden-{campaign.pk}'
    hidden = request.get_signed_cookie(hidden_cookie, default='', salt=hidden_salt) == str(receipt)
    if request.method == 'POST' and request.POST.get('action') == 'hide_result' and already_submitted:
        response = redirect('survey_public')
        response.set_signed_cookie(
            hidden_cookie, str(receipt), salt=hidden_salt, max_age=31536000,
            httponly=True, secure=request.is_secure(), samesite='Lax', path='/quality/survey/',
        )
        return response
    form = WellbeingForm(request.POST if request.method == 'POST' else None, initial={
        'receipt': signing.dumps(str(receipt), salt=salt),
    })
    if request.method == 'POST' and not already_submitted and form.is_valid():
        try:
            signed_receipt = uuid.UUID(signing.loads(form.cleaned_data['receipt'], salt=salt, max_age=86400))
            if signed_receipt != receipt:
                raise ValueError('Receipt mismatch')
        except (ValueError, signing.BadSignature):
            form.add_error(None, 'Не удалось подтвердить отправку. Обновите страницу и разрешите cookie для этого сайта.')
        else:
            if form.cleaned_data['website']:
                form.add_error(None, 'Не удалось отправить анкету.')
            else:
                with transaction.atomic():
                    # Campaign closure and response acceptance serialize on the
                    # same row. Never accept a POST after the campaign closes.
                    locked = SurveyCampaign.objects.select_for_update().get(pk=campaign.pk)
                    if not locked.is_open:
                        return redirect('survey_public')
                    SurveyResponse.objects.get_or_create(
                        receipt=receipt,
                        defaults={
                            'campaign': locked,
                            'submitted_on': today,
                            'answers': [form.cleaned_data[f'q{i}'] for i in range(1, 11)],
                            'additional_answers': {str(i): form.cleaned_data[f'q{i}'] for i in range(11, 14)},
                            **{key: form.cleaned_data[key] for key in ('department', 'category', 'tenure', 'schedule', 'combined_roles', 'comment')},
                        },
                    )
                return redirect('survey_public')
    response = render(request, 'core/survey_public.html', {
        'campaign': campaign, 'form': form, 'already_submitted': already_submitted,
        'personal_result': burnout_self_assessment(previous.answers) if already_submitted and not hidden else None,
        'personal_result_hidden': already_submitted and hidden,
    })
    response.set_signed_cookie(
        cookie, str(receipt), salt=salt, max_age=31536000,
        httponly=True, secure=request.is_secure(), samesite='Lax',
        path='/quality/survey/',
    )
    # HTML forms need a same-origin Origin/Referer for Django's HTTPS CSRF
    # checks. Still disclose no referrer when navigating to another site.
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, noarchive'
    return response


def report_period(query):
    """Resolve live auto filters while keeping old bookmarked period URLs valid."""
    legacy = query.get('period')
    if 'year' in query or 'half' in query:
        filters = {'year': query.get('year') or 'auto', 'half': query.get('half') or 'auto'}
        year, half = selected_halfyear(**filters)
    elif legacy:
        year, half = selected_halfyear(legacy)
        filters = {'year': str(year), 'half': str(half)}
    else:
        filters = {'year': 'auto', 'half': 'auto'}
        year, half = selected_halfyear()
    return year, half, filters


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def survey_dashboard(request):
    if not admin_only(request.user):
        return HttpResponseForbidden('Раздел доступен только сисадмину.')
    campaigns = SurveyCampaign.objects.all()
    selected_id = request.GET.get('campaign')
    if selected_id and not selected_id.isdigit():
        return HttpResponseBadRequest('Некорректная анкета.')
    campaign = get_object_or_404(campaigns, pk=selected_id) if selected_id else campaigns.first()
    try:
        year, half, filters = report_period(request.GET)
    except ValueError as error:
        return HttpResponseBadRequest(str(error))
    form = CampaignSettingsForm(request.POST or None, instance=campaign) if campaign else None
    if request.method == 'POST' and campaign and form.is_valid():
        with transaction.atomic():
            SurveyCampaign.objects.select_for_update().get(pk=campaign.pk)
            form.save()
            AuditLog.objects.create(user=request.user, action='Изменены настройки анонимного опроса', object_label=campaign.title)
        messages.success(request, 'Настройки опроса сохранены.')
        return redirect(f'{request.path}?{urlencode({"campaign": campaign.pk, **filters})}')
    periods = halfyear_options(campaign, year) if campaign else []
    template = 'core/survey_report.html' if request.GET.get('refresh') == '1' else 'core/survey_dashboard.html'
    return render(request, template, {
        'campaign': campaign, 'campaigns': campaigns, 'settings_form': form,
        'period_options': [item for item in periods if item['year'] == year],
        'year_options': sorted({item['year'] for item in periods} | {year}, reverse=True),
        'selected_year': filters['year'], 'selected_half': filters['half'],
        'report_query': urlencode(filters),
        'selected_period': f'{year}-{half}',
        'all_responses_count': campaign.responses.count() if campaign else 0,
        'updated_at': timezone.localtime(),
        'analytics': survey_analytics(campaign, year, half) if campaign else None,
    })


@login_required
@never_cache
@require_http_methods(['GET'])
def survey_pdf(request, pk):
    if not admin_only(request.user):
        return HttpResponseForbidden('Раздел доступен только сисадмину.')
    campaign = get_object_or_404(SurveyCampaign, pk=pk)
    try:
        year, half, _ = report_period(request.GET)
    except ValueError as error:
        return HttpResponseBadRequest(str(error))
    from .survey_pdf import build_survey_pdf
    response = HttpResponse(build_survey_pdf(campaign, survey_analytics(campaign, year, half)), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="wellbeing-{year}-H{half}.pdf"'
    return response
