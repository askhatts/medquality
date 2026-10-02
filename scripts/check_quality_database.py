"""Read-only smoke checks for the portal's active or shadow database."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client

from core.models import Assignment, TestAttempt


assert User.objects.count() > 0
assert Assignment.objects.count() > 0
assert TestAttempt.objects.count() > 0
assert User.objects.filter(is_active=True).exclude(password="").exists()
connection.check_constraints()
client = Client(HTTP_HOST="oncomap-abai.kz")
for route, expected in (("/quality/health/", 200), ("/quality/login/", 200)):
    response = client.get(route)
    assert response.status_code == expected, (route, response.status_code)
print(
    "Database smoke OK:", connection.vendor,
    "users", User.objects.count(),
    "assignments", Assignment.objects.count(),
    "attempts", TestAttempt.objects.count(),
)
