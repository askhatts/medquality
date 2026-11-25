"""Resolve reported grades without fabricating lost test answers or timestamps."""
from .models import ResultRecovery


def learning_result(assignment, attempts=None):
    attempts = list(assignment.attempts.all()) if attempts is None else attempts
    last = attempts[-1] if attempts else None
    try:
        recovery = assignment.recovered_result
    except ResultRecovery.DoesNotExist:
        recovery = None
    if recovery:
        return {
            'last_attempt': last, 'score': recovery.score,
            # A recovery registration date is NOT a test submission date.
            'result_at': recovery.previous_completed_at,
            'result_source': 'Восстановлено администратором',
            'recovered': True, 'recorded_at': recovery.recorded_at,
            'attempts_used': len(attempts),
        }
    return {
        'last_attempt': last, 'score': last.score if last else None,
        'result_at': last.completed_at if last else None,
        'result_source': 'Сохранённая попытка' if last else '',
        'recovered': False, 'recorded_at': None, 'attempts_used': len(attempts),
    }
