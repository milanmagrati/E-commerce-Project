"""
URL configuration for myproject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from hrm.views import iclock_cdata, iclock_getrequest, iclock_devicecmd

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('accounts/', include('accounts.urls')),
    path('ncm/', include('ncm.urls')),
    path('pnd/', include('pick_and_drop.urls')),
    path('chat/', include('chat.urls')),
    path('hrm/', include('hrm.urls')),
    path('todo/', include('todo.urls')),
    path('store/', include('store.urls')),
    path('api/integrations/', include('integrations.urls')),
    path('bill-rewards/', include('bill_rewards.urls')),
    path('imports/', include('google_sheets.urls')),
    path('trendy-crm/', include('trendycrm.urls')),

    # ZKTeco ADMS endpoints at root level (device pushes to /iclock/...)
    path('iclock/cdata', iclock_cdata, name='iclock_cdata_root'),
    path('iclock/getrequest', iclock_getrequest, name='iclock_getrequest_root'),
    path('iclock/devicecmd', iclock_devicecmd, name='iclock_devicecmd_root'),

] 
from django.urls import re_path
from django.views.static import serve

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
else:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
        re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),
    ]
