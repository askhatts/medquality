import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .models import AuditLog, SurveyCampaign, SurveyResponse
from .surveys import CampaignSettingsForm, WellbeingForm, survey_analytics


def admin_only(user):
    return user.is_active and user.is_authenticated and getattr(getattr(user, 'profile', None), 'role', None) == 'ADMIN'


def receipt_cookie(campaign):
    return f'wellbeing_{campaign.pk}'


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
    already_submitted = SurveyResponse.objects.filter(campaign=campaign, receipt=receipt).exists()
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
                            'answers': [form.cleaned_data[f'q{i}'] for i in range(1, 11)],
                            'additional_answers': {str(i): form.cleaned_data[f'q{i}'] for i in range(11, 14)},
                            **{key: form.cleaned_data[key] for key in ('department', 'category', 'tenure', 'schedule', 'combined_roles', 'comment')},
                        },
                    )
                return redirect('survey_public')
    response = render(request, 'core/survey_public.html', {
        'campaign': campaign, 'form': form, 'already_submitted': already_submitted,
    })
    response.set_signed_cookie(
        cookie, str(receipt), salt=salt, max_age=31536000,
        httponly=True, secure=request.is_secure(), samesite='Lax',
        path='/quality/survey/',
    )
    response['Referrer-Policy'] = 'no-referrer'
    return response


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def survey_dashboard(request):
    if not admin_only(request.user):
        return HttpResponseForbidden('Раздел доступен только сисадмину.')
    campaigns = SurveyCampaign.objects.all()
    selected_id = request.GET.get('campaign')
    campaign = get_object_or_404(campaigns, pk=selected_id) if selected_id else campaigns.first()
    form = CampaignSettingsForm(request.POST or None, instance=campaign) if campaign else None
    if request.method == 'POST' and campaign and form.is_valid():
        with transaction.atomic():
            SurveyCampaign.objects.select_for_update().get(pk=campaign.pk)
            form.save()
            AuditLog.objects.create(user=request.user, action='Изменены настройки анонимного опроса', object_label=campaign.title)
        messages.success(request, 'Настройки опроса сохранены.')
        return redirect(f'{request.path}?campaign={campaign.pk}')
    return render(request, 'core/survey_dashboard.html', {
        'campaign': campaign, 'campaigns': campaigns, 'settings_form': form,
        'analytics': survey_analytics(campaign) if campaign else None,
    })


@login_required
@never_cache
@require_http_methods(['GET'])
def survey_pdf(request, pk):
    if not admin_only(request.user):
        return HttpResponseForbidden('Раздел доступен только сисадмину.')
    campaign = get_object_or_404(SurveyCampaign, pk=pk)
    from .survey_pdf import build_survey_pdf
    response = HttpResponse(build_survey_pdf(campaign, survey_analytics(campaign)), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="wellbeing-{campaign.slug}.pdf"'
    return response
