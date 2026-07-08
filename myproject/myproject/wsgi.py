"""
WSGI config for myproject project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

application = get_wsgi_application()

# Automatically apply migrations on server startup
try:
    from django.core.management import call_command
    call_command('migrate', interactive=False)
except Exception as e:
    print(f"Auto-migration failed: {e}")
