from celery import task
from django.core import management


@task()
def import_cnml(*args, **kwargs):
    """
    runs "python manage.py import_cnml"
    """
    management.call_command('import_cnml', *args, **kwargs)
