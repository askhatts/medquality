"""Verify persisted test outcomes only in an isolated load-test database."""
import json
import sys
from pathlib import Path
import django
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
django.setup()
from django.conf import settings
from core.models import Assignment, TestAttempt
assert settings.DATABASES['default']['NAME'].startswith('quality_loadtest_')
run = sys.argv[1] if len(sys.argv) > 1 else 'full'
assert run in ('initial', 'full', 'tuned')
assignments = Assignment.objects.filter(demo_key__startswith=f'isolated-load-{run}-')
attempts = TestAttempt.objects.filter(assignment__in=assignments)
print(json.dumps({'assigned': assignments.count(), 'completed': assignments.filter(status='COMPLETED').count(),
                  'attempts': attempts.count(), 'score_100': attempts.filter(score=100, passed=True).count(),
                  'saved_snapshots': attempts.exclude(answers_snapshot=[]).count(),
                  'original_attempts': TestAttempt.objects.exclude(assignment__demo_key__startswith='isolated-load-').count()}))
