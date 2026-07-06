"""
WSGI config for myproject project.

NOTE: This app uses Django Channels (WebSockets) for real-time follow-up collaboration.
WebSockets ONLY work with the ASGI server (Daphne).

In production, always start with:
    daphne -b 0.0.0.0 -p 8000 myproject.asgi:application

NOT:
    gunicorn myproject.wsgi:application  ← WebSockets will NOT work
    python manage.py runserver            ← OK for local dev only
"""

import os
import warnings

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

warnings.warn(
    "\n\n⚠️  WSGI mode detected — WebSockets (real-time features) will NOT work.\n"
    "   Start the server with Daphne instead:\n"
    "   daphne -b 0.0.0.0 -p 8000 myproject.asgi:application\n",
    RuntimeWarning,
    stacklevel=2
)

application = get_wsgi_application()

# Automatically apply migrations on server startup to prevent deployment errors
try:
    from django.core.management import call_command
    call_command('migrate', interactive=False)
except Exception as e:
    print(f"Auto-migration failed: {e}")
