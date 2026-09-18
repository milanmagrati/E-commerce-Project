"""
WSGI config for myproject project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/

⚠️  PRODUCTION NOTE:
    DO NOT run migrations here. When multiple worker processes start
    simultaneously (cPanel, gunicorn, uWSGI), they ALL execute this file,
    causing MySQL table-lock collisions that crash every worker → 500 errors.

    Run migrations manually BEFORE restarting workers:
        python manage.py migrate
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

application = get_wsgi_application()

