"""
ASGI config for myproject project.

Exposes the ASGI callable as a module-level variable named ``application``.
This file handles BOTH HTTP and WebSocket (Django Channels) connections.

Production start command:
    daphne -b 0.0.0.0 -p 8000 myproject.asgi:application
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

# Django must be fully set up before importing routing/consumers
django.setup()

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import dashboard.routing

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            dashboard.routing.websocket_urlpatterns
        )
    ),
})
