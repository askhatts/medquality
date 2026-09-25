from django.contrib import admin
from .models import AuditLog, Assignment, Course, Department, EmployeeType, InternalDocument, Lesson, Profile, QualityDirection, QualityRequirement, Question, Test, TestAttempt
for model in [Department, EmployeeType, Profile, QualityDirection, QualityRequirement, Course, Lesson, InternalDocument, Test, Question, Assignment, TestAttempt, AuditLog]: admin.site.register(model)
