from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/follow_ups/$', consumers.FollowUpConsumer.as_asgi()),
]
