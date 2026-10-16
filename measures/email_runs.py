"""
Bookkeeping for the periodic emails (monthly report, personal digest): one EmailRun per
(kind, period) and one EmailLog per recipient, so a mailing is never sent twice and a
failed one can be resumed.

Scheduling policy (see ``check_scheduled_emails`` in tasks.py):
    monthly_report   day REPORT_DAY..REPORT_LAST_DAY, reports the PREVIOUS month, to every contributor
    activity_digest  day DIGEST_DAY..DIGEST_LAST_DAY, last 30 days, to people active in the last 60
Both only between SEND_HOUR_FROM and SEND_HOUR_TO (Europe/Madrid), and only when enabled in settings.
"""
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

TZ = 'Europe/Madrid'
REPORT_DAY, REPORT_LAST_DAY = 2, 9
DIGEST_DAY, DIGEST_LAST_DAY = 16, 23
SEND_HOUR_FROM, SEND_HOUR_TO = 9, 20


def json_safe(data):
    """Round-trip through JSON so the payload is storable (drops the chart flags)."""
    data = {k: v for k, v in (data or {}).items() if k != 'charts'}
    return json.loads(json.dumps(data, default=str))


def start_run(kind, period):
    """
    Claim the run for (kind, period). Returns (run, claimed): ``claimed`` is False when it is
    already sent/sending (someone else has it). A failed or partial run is re-claimed so it can resume.
    """
    from .models import EmailRun
    try:
        with transaction.atomic():
            return EmailRun.objects.create(kind=kind, period=period), True
    except IntegrityError:
        run = EmailRun.objects.get(kind=kind, period=period)
        if run.status in ('failed', 'partial'):
            run.status = 'sending'
            run.finished_at = None
            run.save(update_fields=['status', 'finished_at'])
            return run, True
        return run, False


def finish_run(run, recipients_count, sent, failed, notes=''):
    run.recipients_count = max(run.recipients_count, recipients_count)
    run.sent_count = run.logs.filter(status='sent').count()
    run.failed_count = failed
    run.status = 'sent' if not failed else ('partial' if run.sent_count else 'failed')
    run.finished_at = timezone.now()
    run.notes = notes
    run.save()
    return run


def already_sent(kind, period):
    """Lowercase emails that already received this mailing."""
    from .models import EmailLog
    return {e.lower() for e in EmailLog.objects.filter(kind=kind, period=period, status='sent')
            .values_list('email', flat=True)}


def log_email(kind, period, email, status, run=None, user_id=None, variant='', subject='', error='', payload=None):
    from .models import EmailLog
    obj, _ = EmailLog.objects.update_or_create(
        kind=kind, period=period, email=email,
        defaults={'run': run, 'user_id': user_id, 'variant': variant, 'subject': subject[:300],
                  'status': status, 'error': error[:2000], 'payload': json_safe(payload)})
    return obj


def local_now():
    return datetime.now(ZoneInfo(TZ))
