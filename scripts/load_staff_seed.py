"""Prepare copied staff accounts for an isolated load test. Never use on live DB."""
import json
import secrets
import sys
from datetime import timedelta
from pathlib import Path

import django
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
django.setup()
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone
from core.models import Assignment, Profile, Test, TestAttempt

database = settings.DATABASES['default']['NAME']
if not database.startswith('quality_loadtest_'):
    raise SystemExit('Refusing to modify a non-loadtest database')
with connection.cursor() as cursor:
    cursor.execute('SELECT current_database()')
    assert cursor.fetchone()[0] == database
run = sys.argv[1] if len(sys.argv) > 1 else 'initial'
assert run in ('initial', 'full', 'tuned'), 'Unknown bounded test phase'
if Assignment.objects.filter(demo_key__startswith=f'isolated-load-{run}-').exists():
    raise SystemExit('This phase was already seeded')
password = secrets.token_urlsafe(24)
encoded = make_password(password)
tests = list(Test.objects.filter(status='PUBLISHED', is_current=True).prefetch_related('questions'))
assert tests, 'No published course tests'
users = list(User.objects.filter(is_active=True).select_related('profile').order_by('id'))
manifest = {'database': database, 'password': password, 'users': []}
for index, user in enumerate(users):
    user.password = encoded
    user.save(update_fields=['password'])
    Profile.objects.filter(user=user).update(force_password_change=False)
    Assignment.objects.filter(user=user, status__in=['ASSIGNED', 'IN_PROGRESS', 'OVERDUE']).update(status='CANCELLED')
    test = tests[index % len(tests)]
    assignment = Assignment.objects.create(
        user=user, course=test.course, test_version=test,
        due_date=timezone.localdate() + timedelta(days=7),
        attempts_allowed=2, passing_score=80, is_demo=True,
        demo_key=f'isolated-load-{run}-{user.pk}',
    )
    answers = [(f'q{q.pk}', str(i)) for q in test.questions.all()
               for i in (q.correct_indexes or [q.correct_index])]
    manifest['users'].append({'username': user.username, 'user_id': user.pk,
                              'assignment': assignment.pk, 'course': test.course_id,
                              'answers': answers, 'role': user.profile.role})
Path('/tmp/load-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
Path('/tmp/load-manifest.json').chmod(0o600)
print(json.dumps({'database': database, 'staff': len(users), 'courses': [t.course_id for t in tests],
                  'existing_attempts': TestAttempt.objects.exclude(assignment__demo_key__startswith='isolated-load-').count()}))
