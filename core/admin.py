from django.contrib import admin
from .models import ResultRecovery
from .models import AuditLog, Assignment, Course, Department, EmployeeType, InternalDocument, Lesson, PasswordResetRequest, Profile, QualityDirection, QualityRequirement, Question, Test, TestAttempt
for model in [Department, EmployeeType, Profile, PasswordResetRequest, QualityDirection, QualityRequirement, Course, Lesson, InternalDocument, Test, Question, Assignment, TestAttempt, AuditLog]: admin.site.register(model)


@admin.register(ResultRecovery)
class ResultRecoveryAdmin(admin.ModelAdmin):
    list_display = ('assignment', 'score', 'recorded_at', 'recorded_by')
    readonly_fields = tuple(field.name for field in ResultRecovery._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
