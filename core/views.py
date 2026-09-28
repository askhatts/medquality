from datetime import datetime
from io import BytesIO
from pathlib import Path
import random

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Prefetch
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils._os import safe_join
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from .models import (Assignment, AuditLog, Course, Department, EmployeeType,
                     InternalDocument, Lesson, Profile, QualityDirection,
                     QualityRequirement, Question, Test, TestAttempt)


MAX_UPLOAD_SIZE = 15 * 1024 * 1024


def profile(user): return Profile.objects.get_or_create(user=user)[0]
def role_in(user, *roles): return profile(user).role in roles
def can_manage(user): return role_in(user, 'ADMIN', 'METHODIST')
def is_head(user): return role_in(user, 'HEAD')
def deny(user): return HttpResponseForbidden('Недостаточно прав для этого раздела.')


def validate_pdf(upload):
    if not upload: return None
    if upload.size > MAX_UPLOAD_SIZE: return 'Размер PDF не должен превышать 15 МБ.'
    if Path(upload.name).suffix.lower() != '.pdf': return 'Разрешены только PDF-файлы.'
    if getattr(upload, 'content_type', '') not in ('application/pdf', 'application/octet-stream'):
        return 'Файл должен иметь тип PDF.'
    if upload.read(5) != b'%PDF-': return 'Содержимое файла не является PDF.'
    upload.seek(0)
    return None


@login_required
def media_file(request, path):
    try:
        filename = safe_join(settings.MEDIA_ROOT, path)
        return FileResponse(
            open(filename, 'rb'),
            as_attachment=request.GET.get('download') == '1',
            filename=Path(filename).name,
        )
    except (FileNotFoundError, ValueError):
        raise Http404


def login_view(request):
    if request.user.is_authenticated: return redirect('dashboard')
    error = None
    if request.method == 'POST':
        user = authenticate(request, username=request.POST.get('username'), password=request.POST.get('password'))
        if user and user.is_active:
            login(request, user)
            request.session.set_expiry(300)
            display_name = user.get_full_name().strip() or user.username
            messages.success(request, f'Добро пожаловать, {display_name}! Ваш логин: {user.username}.')
            AuditLog.objects.create(user=user, action='Вход в систему')
            return redirect('password' if profile(user).force_password_change else 'dashboard')
        error = 'Неверный логин или пароль.'
    return render(request, 'core/login.html', {'error': error})


def logout_view(request): logout(request); return redirect('login')


@login_required
def session_ping(request):
    request.session.modified = True
    return HttpResponse(status=204)


@login_required
def change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(); update_session_auth_hash(request, user)
        p = profile(user); p.force_password_change = False; p.save(update_fields=['force_password_change'])
        return redirect('dashboard')
    return render(request, 'core/password.html', {'form': form})


def visible_assignments(user):
    qs = Assignment.objects.select_related('user', 'user__profile', 'course', 'course__direction', 'test_version').prefetch_related('attempts')
    if role_in(user, 'ADMIN', 'METHODIST'): return qs
    if is_head(user): return qs.filter(user__profile__department=profile(user).department)
    return qs.filter(user=user)


def filtered_assignments(request):
    assignments = visible_assignments(request.user)
    for field, lookup in [('direction','course__direction_id'),('requirement','course__quality_requirements__id'),('course','course_id'),('department','user__profile__department_id'),('employee','user_id')]:
        value = request.GET.get(field)
        if value: assignments = assignments.filter(**{lookup: value})
    if request.GET.get('date_from'): assignments = assignments.filter(assigned_at__date__gte=request.GET['date_from'])
    if request.GET.get('date_to'): assignments = assignments.filter(assigned_at__date__lte=request.GET['date_to'])
    return assignments.distinct()


@login_required
def dashboard(request):
    p = profile(request.user); assignments = visible_assignments(request.user)
    if p.role == 'EMPLOYEE': return redirect('directions')
    people = Profile.objects.filter(user__is_active=True)
    if p.role == 'HEAD': people = people.filter(department=p.department)
    return render(request, 'core/dashboard.html', {'profile': p, 'assignments': assignments[:8], 'overdue': [a for a in assignments if a.is_overdue], 'completed': assignments.filter(status='COMPLETED').count(), 'in_progress': assignments.filter(status='IN_PROGRESS').count(), 'assigned': assignments.filter(status='ASSIGNED').count(), 'people_count': people.count()})


@login_required
def directions(request):
    items = QualityDirection.objects.filter(active=True).prefetch_related('courses', 'requirements')
    return render(request, 'core/directions.html', {'directions': items})


@login_required
def direction_detail(request, pk):
    direction = get_object_or_404(QualityDirection.objects.prefetch_related(
        Prefetch('courses', queryset=Course.objects.filter(active=True), to_attr='active_courses')
    ), pk=pk, active=True)
    return render(request, 'core/direction.html', {'direction': direction})


@login_required
def courses(request):
    return redirect('directions')


@login_required
def course_manage(request):
    if not can_manage(request.user): return deny(request.user)
    courses = Course.objects.select_related('direction').prefetch_related('lessons', 'tests', 'internal_documents')
    return render(request, 'core/course_manage.html', {'courses': courses})


@login_required
def course_form(request, pk=None):
    if not can_manage(request.user): return deny(request.user)
    course = get_object_or_404(Course, pk=pk) if pk else None
    if request.method == 'POST':
        course = course or Course()
        category_title = request.POST.get('category', '').strip() or 'Без категории'
        category = QualityDirection.objects.filter(title__iexact=category_title).first()
        if not category:
            category = QualityDirection.objects.create(title=category_title, active=True)
        course.direction = category
        course.title = request.POST['title'].strip()
        course.description = request.POST.get('description', '').strip()
        course.active = request.POST.get('active') == 'on'
        course.save()
        AuditLog.objects.create(
            user=request.user,
            action='Курс создан' if pk is None else 'Курс обновлён',
            object_label=course.title,
        )
        messages.success(request, 'Курс сохранён. Теперь можно добавить материалы и тест.')
        return redirect('course_content', course_id=course.id)
    return render(request, 'core/course_form.html', {
        'course': course,
        'directions': QualityDirection.objects.filter(active=True),
    })


@login_required
def course_content(request, course_id):
    if not can_manage(request.user): return deny(request.user)
    visible_lessons = Lesson.objects.exclude(category='INTERNAL')
    course = get_object_or_404(Course.objects.select_related('direction').prefetch_related(
        Prefetch('lessons', queryset=visible_lessons, to_attr='visible_lessons'),
        'tests', 'internal_documents',
    ), pk=course_id)
    documents = InternalDocument.objects.filter(status='ACTIVE').select_related('direction').order_by('title')
    return render(request, 'core/course_content.html', {'course': course, 'documents': documents})


@login_required
def course_document_link(request, course_id):
    if not can_manage(request.user): return deny(request.user)
    course = get_object_or_404(Course, pk=course_id)
    if request.method == 'POST':
        documents = InternalDocument.objects.filter(
            pk__in=request.POST.getlist('documents'), status='ACTIVE'
        )
        course.internal_documents.add(*documents)
        if documents:
            AuditLog.objects.create(user=request.user, action='Документы подключены к курсу', object_label=course.title)
            messages.success(request, f'Документы добавлены в курс «{course.title}».')
    return redirect('course_content', course_id=course.id)


@login_required
def course_document_unlink(request, course_id, document_id):
    if not can_manage(request.user): return deny(request.user)
    course = get_object_or_404(Course, pk=course_id)
    document = get_object_or_404(InternalDocument, pk=document_id)
    if request.method == 'POST':
        course.internal_documents.remove(document)
        AuditLog.objects.create(user=request.user, action='Документ откреплён от курса', object_label=f'{course.title}: {document.title}')
    return redirect('course_content', course_id=course.id)


@login_required
def lesson_create(request, course_id):
    if not can_manage(request.user): return deny(request.user)
    course = get_object_or_404(Course, pk=course_id)
    if request.method == 'POST':
        uploads = request.FILES.getlist('files') or ([request.FILES['file']] if request.FILES.get('file') else [])
        for upload in uploads:
            error = validate_pdf(upload)
            if error: messages.error(request, f'{upload.name}: {error}'); continue
            Lesson.objects.create(course=course, title=Path(upload.name).stem, category=request.POST.get('category','LESSON'), kind='DOC', order=int(request.POST.get('order') or 1), file=upload)
        if not uploads:
            Lesson.objects.create(course=course, title=request.POST['title'].strip(), category=request.POST.get('category','LESSON'), kind=request.POST.get('kind','LINK'), order=int(request.POST.get('order') or 1), url=request.POST.get('url','').strip())
        AuditLog.objects.create(user=request.user, action='Добавлен материал курса', object_label=course.title)
    return redirect('course_content', course_id=course.id)


@login_required
def lesson_delete(request, pk):
    if not can_manage(request.user): return deny(request.user)
    lesson = get_object_or_404(Lesson, pk=pk); course = lesson.course
    if request.method == 'POST': lesson.delete()
    return redirect('course_content', course_id=course.id)


@login_required
def lesson_edit(request, pk):
    if not can_manage(request.user): return deny(request.user)
    lesson = get_object_or_404(Lesson, pk=pk)
    if request.method == 'POST':
        lesson.title=request.POST['title'].strip(); lesson.category=request.POST['category']; lesson.kind=request.POST['kind']; lesson.order=max(1,int(request.POST.get('order') or 1)); lesson.url=request.POST.get('url','').strip()
        upload=request.FILES.get('file')
        if upload:
            error=validate_pdf(upload)
            if error: messages.error(request,error); return redirect('lesson_edit',pk=pk)
            lesson.file=upload
        lesson.save(); return redirect('course_content', course_id=lesson.course_id)
    return render(request,'core/lesson_form.html',{'lesson':lesson})


def current_test(course, published_only=False):
    qs = course.tests.filter(is_current=True)
    if published_only: qs = qs.filter(status='PUBLISHED')
    return qs.first()


def editable_test(course):
    test = current_test(course)
    if not test:
        return Test.objects.create(course=course, title=f'Итоговый тест: {course.title}', version=1)
    if test.status == 'PUBLISHED' and TestAttempt.objects.filter(assignment__test_version=test).exists():
        with transaction.atomic():
            test.is_current = False; test.save(update_fields=['is_current'])
            clone = Test.objects.create(course=course, title=test.title, instructions=test.instructions, version=test.version + 1, status='DRAFT', is_current=True)
            for q in test.questions.all():
                Question.objects.create(test=clone, text=q.text, question_type=q.question_type, options=q.options, correct_index=q.correct_index, correct_indexes=q.correct_indexes, points=q.points, explanation=q.explanation, order=q.order)
            return clone
    return test


@login_required
def test_editor(request, course_id):
    if not can_manage(request.user): return deny(request.user)
    course = get_object_or_404(Course, pk=course_id)
    test = current_test(course) or Test.objects.create(course=course, title=f'Итоговый тест: {course.title}')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'publish':
            if not test.questions.exists(): messages.error(request, 'Добавьте хотя бы один вопрос.')
            else:
                test.status = 'PUBLISHED'; test.save(update_fields=['status']); messages.success(request, f'Опубликована версия {test.version}.')
        elif action in ('save_test', 'save_question'):
            source_question = Question.objects.filter(pk=request.POST.get('question_id')).first() if request.POST.get('question_id') else None
            test = editable_test(course)
            if action == 'save_test':
                test.title = request.POST['title'].strip(); test.instructions = request.POST.get('instructions','').strip(); test.save()
            else:
                question = test.questions.filter(order=source_question.order, text=source_question.text).first() if source_question else Question(test=test)
                if question is None: question = Question(test=test)
                qtype = request.POST.get('question_type','SINGLE')
                options = ['Верно','Неверно'] if qtype == 'TRUE_FALSE' else [x.strip() for x in request.POST.get('options','').splitlines() if x.strip()]
                correct = [int(x) for x in request.POST.getlist('correct_indexes') if x.isdigit()]
                if qtype != 'MULTIPLE' and correct: correct = correct[:1]
                if not options or not correct or any(x >= len(options) for x in correct):
                    messages.error(request, 'Заполните варианты и выберите корректный ответ.')
                else:
                    question.text=request.POST['text'].strip(); question.question_type=qtype; question.options=options; question.correct_indexes=correct; question.correct_index=correct[0]; question.points=max(1,int(request.POST.get('points') or 1)); question.explanation=request.POST.get('explanation','').strip(); question.order=int(request.POST.get('order') or test.questions.count()+1); question.save()
        return redirect('test_editor', course_id=course.id)
    return render(request, 'core/test_editor.html', {'course': course, 'test': test, 'types': Question.TYPES})


@login_required
def test_preview(request, course_id):
    if not can_manage(request.user): return deny(request.user)
    course=get_object_or_404(Course,pk=course_id); test=current_test(course)
    return render(request,'core/test_preview.html',{'course':course,'test':test})


@login_required
def question_delete(request, pk):
    if not can_manage(request.user): return deny(request.user)
    source = get_object_or_404(Question, pk=pk); course = source.test.course
    if request.method == 'POST':
        test = editable_test(course)
        target = test.questions.filter(order=source.order, text=source.text).first()
        if target: target.delete()
    return redirect('test_editor', course_id=course.id)


@login_required
def course_detail(request, pk):
    visible_lessons = Lesson.objects.exclude(category='INTERNAL')
    course = get_object_or_404(
        Course.objects.prefetch_related(
            Prefetch('lessons', queryset=visible_lessons, to_attr='visible_lessons'),
            'internal_documents',
        ),
        pk=pk,
    )
    assignments = Assignment.objects.filter(course=course, user=request.user).prefetch_related('attempts')
    assignment = assignments.filter(status__in=('ASSIGNED', 'IN_PROGRESS', 'OVERDUE')).first() or assignments.first()
    test = assignment.test_version if assignment and assignment.test_version_id else current_test(course, True)
    return render(request, 'core/course.html', {'course': course, 'assignment': assignment, 'test': test})


@login_required
def acknowledge(request, pk):
    assignment = get_object_or_404(Assignment, pk=pk, user=request.user)
    if request.method == 'POST':
        if assignment.is_overdue:
            if assignment.status != 'OVERDUE':
                assignment.status = 'OVERDUE'; assignment.save(update_fields=['status'])
            messages.error(request, 'Срок прохождения курса истёк. Обратитесь к методисту для нового назначения.')
            return redirect('course', pk=assignment.course_id)
        assignment.acknowledged_at = timezone.now(); assignment.status = 'IN_PROGRESS'; assignment.save()
        AuditLog.objects.create(user=request.user, action='Начато обучение', object_label=assignment.course.title)
    return redirect('course', pk=assignment.course_id)


@login_required
def take_test(request, pk):
    course = get_object_or_404(Course, pk=pk)
    assignments = Assignment.objects.filter(course=course, user=request.user)
    assignment = assignments.filter(status__in=('ASSIGNED', 'IN_PROGRESS', 'OVERDUE')).first() or assignments.first()
    if not assignment: return deny(request.user)
    if assignment.is_overdue:
        if assignment.status != 'OVERDUE':
            assignment.status = 'OVERDUE'; assignment.save(update_fields=['status'])
        messages.error(request, 'Срок теста истёк. Прохождение заблокировано.')
        return redirect('course', pk=pk)
    test = assignment.test_version or current_test(course, True)
    if not test: return HttpResponse('Итоговый тест ещё не опубликован.', status=409)
    if assignment.status == 'COMPLETED' or assignment.attempts_left == 0: return redirect('course', pk=pk)
    if request.method == 'POST':
        questions=list(test.questions.all()); total=sum(q.points for q in questions); earned=0; correct_count=0; snapshot=[]
        for q in questions:
            selected = request.POST.getlist(f'q{q.id}')
            selected_indexes = sorted(int(x) for x in selected if x.isdigit())
            expected = sorted(q.correct_indexes or [q.correct_index])
            correct = selected_indexes == expected
            if correct: earned += q.points; correct_count += 1
            snapshot.append({'question_id':q.id,'question':q.text,'type':q.question_type,'options':q.options,'selected':selected_indexes,'correct':expected,'is_correct':correct,'points':q.points if correct else 0,'max_points':q.points})
        score = round(100 * earned / total) if total else 0; passed = score >= assignment.passing_score
        attempt=TestAttempt.objects.create(assignment=assignment, number=assignment.attempts.count()+1, correct_answers=correct_count, score=score, passed=passed, answers_snapshot=snapshot)
        if passed: assignment.status='COMPLETED'; assignment.completed_at=timezone.now()
        elif assignment.attempts_left == 0: assignment.status='FAILED'
        else: assignment.status='IN_PROGRESS'
        assignment.save(); AuditLog.objects.create(user=request.user, action='Пройден тест', object_label=f'{course.title}: {score}%')
        return render(request, 'core/test.html', {'test':test,'assignment':assignment,'result':attempt})
    question_items = []
    for question in test.questions.all():
        options = list(enumerate(question.options))
        random.SystemRandom().shuffle(options)
        question_items.append({'question': question, 'options': options})
    return render(request, 'core/test.html', {
        'test': test, 'assignment': assignment, 'question_items': question_items,
    })


@login_required
def document_bank(request):
    documents=InternalDocument.objects.select_related('direction','requirement','department').prefetch_related('courses')
    if request.GET.get('q'): documents=documents.filter(title__icontains=request.GET['q'])
    for key, lookup in [('direction','direction_id'),('requirement','requirement_id'),('department','department_id'),('document_type','document_type'),('status','status')]:
        if request.GET.get(key): documents=documents.filter(**{lookup:request.GET[key]})
    return render(request,'core/document_bank.html',{'documents':documents,'directions':QualityDirection.objects.all(),'requirements':QualityRequirement.objects.all(),'departments':Department.objects.all(),'document_types':InternalDocument.TYPES,'can_manage':can_manage(request.user)})


@login_required
def document_form(request, pk=None):
    if not can_manage(request.user): return deny(request.user)
    document=get_object_or_404(InternalDocument,pk=pk) if pk else None
    if request.method=='POST':
        upload=request.FILES.get('file')
        if upload:
            error=validate_pdf(upload)
            if error: messages.error(request,error); return redirect('document_edit',pk=pk) if pk else redirect('document_new')
        if not document and not upload: return HttpResponse('Выберите PDF-файл.',status=400)
        document=document or InternalDocument()
        for field in ('title','number','version','owner','tags','status','document_type'): setattr(document,field,request.POST.get(field,'').strip())
        document.direction_id=request.POST.get('direction') or None
        if 'requirement' in request.POST: document.requirement_id=request.POST.get('requirement') or None
        document.department_id=request.POST.get('department') or None
        document.approved_at=request.POST.get('approved_at') or None; document.review_due=request.POST.get('review_due') or None
        if upload: document.file=upload
        document.save(); document.courses.set(request.POST.getlist('courses')); return redirect('document_bank')
    return render(request,'core/document_form.html',{'document':document,'directions':QualityDirection.objects.all(),'requirements':QualityRequirement.objects.all(),'departments':Department.objects.all(),'courses':Course.objects.all(),'document_types':InternalDocument.TYPES})


@login_required
def employees(request):
    if not can_manage(request.user): return deny(request.user)
    return render(request, 'core/employees.html', {'employees': Profile.objects.select_related('user','department','employee_type').order_by('user__last_name'), 'departments': Department.objects.all(), 'types': EmployeeType.objects.all()})


@login_required
def employee_form(request, pk=None):
    if not can_manage(request.user): return deny(request.user)
    target = get_object_or_404(Profile, pk=pk) if pk else None
    if request.method == 'POST':
        username=request.POST['username'].strip(); user=target.user if target else User(username=username)
        user.username=username; user.first_name=request.POST['first_name'].strip(); user.last_name=request.POST['last_name'].strip()
        if not target: user.set_password(request.POST.get('password') or User.objects.make_random_password()); user.save(); target=Profile(user=user,force_password_change=True)
        else: user.save()
        target.role=request.POST['role']; target.department_id=request.POST['department']; target.employee_type_id=request.POST['employee_type']; target.position=request.POST['position'].strip(); target.employee_number=request.POST.get('employee_number','').strip(); target.phone=request.POST.get('phone','').strip(); target.save(); return redirect('employees')
    return render(request,'core/employee_form.html',{'employee':target,'departments':Department.objects.all(),'types':EmployeeType.objects.all(),'roles':Profile.ROLES})


@login_required
def assignment_list(request):
    if not can_manage(request.user): return deny(request.user)
    return render(request,'core/assignments.html',{'assignments':visible_assignments(request.user),'courses':Course.objects.filter(active=True),'departments':Department.objects.all(),'employees':Profile.objects.select_related('user').filter(user__is_active=True),'selected_course':request.GET.get('course','')})


@login_required
def create_assignment(request):
    if not can_manage(request.user): return deny(request.user)
    if request.method!='POST': return redirect('assignments')
    course=get_object_or_404(Course,pk=request.POST['course']); test=current_test(course,True)
    if not test: return HttpResponse('Сначала опубликуйте итоговый тест курса.',status=409)
    due=datetime.strptime(request.POST['due_date'],'%Y-%m-%d').date()
    due_time=datetime.strptime(request.POST.get('due_time') or '23:59','%H:%M').time()
    user_ids=set(request.POST.getlist('users')); department_ids=request.POST.getlist('departments')
    user_ids.update(Profile.objects.filter(department_id__in=department_ids,user__is_active=True).values_list('user_id',flat=True))
    if not user_ids: return HttpResponse('Выберите хотя бы один отдел или одного сотрудника.',status=400)
    values={'due_date':due,'due_time':due_time,'passing_score':int(request.POST['passing_score']),'attempts_allowed':int(request.POST['attempts_allowed']),'test_version':test}
    for user_id in user_ids:
        Assignment.objects.filter(
            user_id=user_id, course=course,
            status__in=('ASSIGNED', 'IN_PROGRESS', 'OVERDUE'),
        ).update(status='REASSIGNED')
        Assignment.objects.create(user_id=user_id,course=course,**values)
    AuditLog.objects.create(user=request.user,action='Назначено обучение',object_label=course.title); return redirect('assignments')


@login_required
def reports(request):
    if role_in(request.user,'EMPLOYEE'): return deny(request.user)
    return render(request,'core/reports.html',{'assignments':filtered_assignments(request),'directions':QualityDirection.objects.all(),'requirements':QualityRequirement.objects.all(),'courses':Course.objects.all(),'departments':Department.objects.all(),'employees':Profile.objects.select_related('user').all()})


@login_required
def report_excel(request):
    if role_in(request.user,'EMPLOYEE'): return deny(request.user)
    wb=Workbook(); ws=wb.active; ws.title='История обучения'
    headers=['Цикл','Сотрудник','Тип','Отдел / отделение','Должность','Категория','Курс','Версия теста','Назначено','Срок','Начато','Попытка','Дата попытки','Верных ответов','Оценка, %','Результат попытки','Итоговый статус','Завершено','Демо']
    ws.append(headers); fill=PatternFill('solid',fgColor='103B53')
    for cell in ws[1]: cell.font=Font(color='FFFFFF',bold=True); cell.fill=fill
    for a in filtered_assignments(request):
        p=a.user.profile
        for attempt in list(a.attempts.all()) or [None]:
            ws.append([a.id,a.user.get_full_name() or a.user.username,str(p.employee_type or ''),str(p.department or ''),p.position,a.course.direction.title,a.course.title,a.test_version.version if a.test_version else '',timezone.localtime(a.assigned_at).strftime('%d.%m.%Y %H:%M'),timezone.localtime(a.deadline).strftime('%d.%m.%Y %H:%M'),timezone.localtime(a.acknowledged_at).strftime('%d.%m.%Y %H:%M') if a.acknowledged_at else '',attempt.number if attempt else '',timezone.localtime(attempt.completed_at).strftime('%d.%m.%Y %H:%M') if attempt else '',attempt.correct_answers if attempt else '',attempt.score if attempt else '',('Пройден' if attempt.passed else 'Не пройден') if attempt else '',a.get_status_display(),timezone.localtime(a.completed_at).strftime('%d.%m.%Y %H:%M') if a.completed_at else '','Да' if a.is_demo else 'Нет'])
    for column in ws.columns: ws.column_dimensions[column[0].column_letter].width=min(48,max(14,max(len(str(x.value or '')) for x in column)+2))
    data=BytesIO(); wb.save(data); response=HttpResponse(data.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'); response['Content-Disposition']='attachment; filename="learning-results.xlsx"'; return response
