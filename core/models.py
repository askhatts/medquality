from datetime import datetime, time

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
import re

class Department(models.Model):
    name = models.CharField('Отдел / отделение', max_length=160, unique=True)
    head = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='headed_departments')
    def __str__(self): return self.name

class EmployeeType(models.Model):
    name = models.CharField('Категория сотрудника', max_length=80, unique=True)
    def __str__(self): return self.name

class Profile(models.Model):
    ROLES = [('ADMIN', 'Администратор'), ('METHODIST', 'Методист'), ('HEAD', 'Руководитель отдела'), ('EMPLOYEE', 'Сотрудник')]
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField('Роль', max_length=12, choices=ROLES, default='EMPLOYEE')
    employee_type = models.ForeignKey(EmployeeType, verbose_name='Категория сотрудника', null=True, blank=True, on_delete=models.SET_NULL)
    department = models.ForeignKey(Department, verbose_name='Отдел / отделение', null=True, blank=True, on_delete=models.SET_NULL)
    position = models.CharField('Должность', max_length=120, blank=True)
    employee_number = models.CharField('Табельный номер', max_length=60, blank=True)
    phone = models.CharField('Телефон', max_length=40, blank=True)
    force_password_change = models.BooleanField(default=False)
    def __str__(self): return f'{self.user.get_full_name() or self.user.username} — {self.get_role_display()}'

class PasswordResetRequest(models.Model):
    STATUSES = [
        ('PENDING', 'Ожидает'),
        ('COMPLETED', 'Выполнен'),
        ('DISMISSED', 'Закрыт'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_requests')
    status = models.CharField('Статус', max_length=12, choices=STATUSES, default='PENDING')
    requested_at = models.DateTimeField('Запрошено', auto_now_add=True)
    resolved_at = models.DateTimeField('Обработано', null=True, blank=True)
    resolved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='resolved_password_reset_requests',
    )

    class Meta:
        ordering = ['-requested_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user'], condition=models.Q(status='PENDING'),
                name='unique_pending_password_reset_request',
            ),
        ]

    def __str__(self):
        return f'{self.user.username} — {self.get_status_display()}'

class QualityDirection(models.Model):
    # Технические поля сопоставляют прежние обязательные колонки и не выводятся в портале.
    internal_code = models.CharField(max_length=40, db_column='number', default='', editable=False)
    internal_category = models.CharField(max_length=255, db_column='section', default='', editable=False)
    legacy_status = models.CharField(max_length=10, db_column='status', default='READY', editable=False)
    legacy_reference = models.TextField(db_column='external_regulation', default='', editable=False)
    title = models.CharField('Название', max_length=255)
    description = models.TextField('Описание', blank=True)
    active = models.BooleanField('Активно', default=True)
    updated_at = models.DateField('Обновлено', default=timezone.now)
    def __str__(self): return self.title

class QualityRequirement(models.Model):
    direction = models.ForeignKey(QualityDirection, on_delete=models.CASCADE, related_name='requirements')
    number = models.CharField('Номер требования', max_length=30)
    title = models.CharField('Название', max_length=255)
    owner = models.CharField('Владелец процесса', max_length=255, blank=True)
    curator = models.CharField('Куратор', max_length=255, blank=True)
    source_url = models.URLField('Источник', blank=True)
    evidence = models.JSONField('Необходимые доказательства', default=list, blank=True)
    courses = models.ManyToManyField('Course', blank=True, related_name='quality_requirements')
    active = models.BooleanField('Активно', default=True)
    def __str__(self): return f'{self.number}. {self.title}'
    class Meta: ordering = ['number', 'id']; unique_together = ('direction', 'number')

class Course(models.Model):
    legacy_passing_score = models.PositiveSmallIntegerField(db_column='passing_score', default=80, editable=False)
    direction = models.ForeignKey(QualityDirection, verbose_name='Направление качества', on_delete=models.CASCADE, related_name='courses')
    title = models.CharField('Название курса', max_length=255)
    description = models.TextField('Описание', blank=True)
    active = models.BooleanField('Активен', default=True)
    def __str__(self): return self.title

class Lesson(models.Model):
    CATEGORIES = [('LESSON', 'Учебный материал'), ('NPA_RK', 'НПА РК'), ('INTERNAL', 'Внутренние документы')]
    legacy_version = models.CharField(max_length=40, db_column='version', default='1.0', editable=False)
    legacy_archived = models.BooleanField(db_column='archived', default=False, editable=False)
    TYPES = [('DOC', 'Документ'), ('VIDEO', 'YouTube-видео'), ('LINK', 'Внешняя ссылка')]
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='lessons')
    title = models.CharField('Название урока', max_length=255)
    category = models.CharField('Раздел курса', max_length=12, choices=CATEGORIES, default='LESSON')
    kind = models.CharField('Тип', max_length=8, choices=TYPES, default='DOC')
    order = models.PositiveSmallIntegerField('Порядок', default=1)
    file = models.FileField('Файл', upload_to='lessons/', blank=True)
    url = models.URLField('Ссылка', blank=True)
    def __str__(self): return f'{self.course}: {self.title}'
    @property
    def youtube_embed_url(self):
        if 'youtube.com/watch?v=' in self.url:
            return 'https://www.youtube-nocookie.com/embed/' + self.url.split('watch?v=', 1)[1].split('&', 1)[0]
        if 'youtu.be/' in self.url:
            return 'https://www.youtube-nocookie.com/embed/' + self.url.split('youtu.be/', 1)[1].split('?', 1)[0]
        return self.url
    @property
    def drive_embed_url(self):
        if not self.url or 'drive.google.com' not in self.url:
            return ''
        match = re.search(r'/d/([A-Za-z0-9_-]+)', self.url)
        return f'https://drive.google.com/file/d/{match.group(1)}/preview' if match else ''
    class Meta: ordering = ['order', 'id']

class Test(models.Model):
    legacy_attempts_allowed = models.PositiveSmallIntegerField(db_column='attempts_allowed', default=3, editable=False)
    time_limit_minutes = models.PositiveSmallIntegerField('Лимит времени, минут', default=0)
    STATUSES = [('DRAFT', 'Черновик'), ('PUBLISHED', 'Опубликован')]
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='tests')
    title = models.CharField('Название теста', max_length=255)
    instructions = models.TextField('Инструкция', blank=True)
    version = models.PositiveSmallIntegerField('Версия', default=1)
    status = models.CharField('Статус', max_length=12, choices=STATUSES, default='DRAFT')
    is_current = models.BooleanField('Текущая версия', default=True)
    def __str__(self): return self.title
    class Meta:
        ordering = ['-version']
        unique_together = ('course', 'version')

class Question(models.Model):
    TYPES = [('SINGLE', 'Один правильный ответ'), ('MULTIPLE', 'Несколько правильных ответов'), ('TRUE_FALSE', 'Верно / неверно')]
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField('Вопрос')
    question_type = models.CharField('Тип вопроса', max_length=12, choices=TYPES, default='SINGLE')
    options = models.JSONField('Варианты ответов', default=list)
    correct_index = models.PositiveSmallIntegerField('Номер верного варианта', default=0)
    correct_indexes = models.JSONField('Верные варианты', default=list, blank=True)
    points = models.PositiveSmallIntegerField('Баллы', default=1)
    explanation = models.TextField('Пояснение', blank=True)
    order = models.PositiveSmallIntegerField('Порядок', default=1)
    class Meta: ordering = ['order', 'id']

class InternalDocument(models.Model):
    STATUSES = [('ACTIVE', 'Действует'), ('ARCHIVED', 'Архив')]
    TYPES = [('SOP', 'СОП'), ('ORDER', 'Приказ'), ('POLICY', 'Положение'), ('FORM', 'Форма / чек-лист'), ('OTHER', 'Другое')]
    title = models.CharField('Название', max_length=255)
    number = models.CharField('Номер документа', max_length=80, blank=True)
    version = models.CharField('Версия', max_length=40, default='1.0')
    approved_at = models.DateField('Дата утверждения', null=True, blank=True)
    review_due = models.DateField('Следующий пересмотр', null=True, blank=True)
    owner = models.CharField('Владелец', max_length=255, blank=True)
    direction = models.ForeignKey(QualityDirection, null=True, blank=True, on_delete=models.SET_NULL, related_name='internal_documents')
    requirement = models.ForeignKey(QualityRequirement, null=True, blank=True, on_delete=models.SET_NULL, related_name='documents')
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name='internal_documents')
    courses = models.ManyToManyField(Course, blank=True, related_name='internal_documents')
    file = models.FileField('PDF', upload_to='internal_documents/')
    tags = models.CharField('Теги', max_length=255, blank=True)
    document_type = models.CharField('Тип документа', max_length=10, choices=TYPES, default='OTHER')
    status = models.CharField('Статус', max_length=10, choices=STATUSES, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.title
    class Meta: ordering = ['title']

class Assignment(models.Model):
    STATUS = [('ASSIGNED', 'Назначено'), ('IN_PROGRESS', 'В процессе'), ('COMPLETED', 'Пройдено'), ('OVERDUE', 'Просрочено'), ('FAILED', 'Не пройдено'), ('REASSIGNED', 'Переназначено')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assignments')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='assignments')
    test_version = models.ForeignKey(Test, null=True, blank=True, on_delete=models.PROTECT, related_name='assignments')
    due_date = models.DateField('Срок прохождения')
    due_time = models.TimeField('Время окончания', null=True, blank=True)
    passing_score = models.PositiveSmallIntegerField('Порог прохождения, %', default=80)
    attempts_allowed = models.PositiveSmallIntegerField('Количество попыток', default=3)
    status = models.CharField('Статус', max_length=12, choices=STATUS, default='ASSIGNED')
    assigned_at = models.DateTimeField('Назначено', auto_now_add=True)
    acknowledged_at = models.DateTimeField('Первый просмотр', null=True, blank=True)
    completed_at = models.DateTimeField('Успешно пройдено', null=True, blank=True)
    is_demo = models.BooleanField('Демонстрационные данные', default=False)
    demo_key = models.CharField(max_length=120, null=True, blank=True, unique=True)
    class Meta:
        ordering = ['-assigned_at', '-id']
    @property
    def deadline(self):
        value = datetime.combine(self.due_date, self.due_time or time(23, 59, 59))
        return timezone.make_aware(value, timezone.get_current_timezone())
    @property
    def is_overdue(self): return self.status not in ('COMPLETED', 'FAILED', 'REASSIGNED') and timezone.now() > self.deadline
    @property
    def attempts_left(self): return max(0, self.attempts_allowed - self.attempts.count())

class TestAttempt(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='attempts')
    number = models.PositiveSmallIntegerField('Номер попытки')
    correct_answers = models.PositiveSmallIntegerField('Верных ответов', default=0)
    score = models.PositiveSmallIntegerField('Оценка, %')
    passed = models.BooleanField('Пройден')
    answers_snapshot = models.JSONField('Снимок ответов', default=list, blank=True)
    completed_at = models.DateTimeField('Дата прохождения', auto_now_add=True)
    class Meta: unique_together = ('assignment', 'number'); ordering = ['number']

class AuditLog(models.Model):
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=120)
    object_label = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
