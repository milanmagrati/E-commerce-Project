"""
URL configuration for myproject project.
"""
from django.contrib import admin
from django.urls import path, include, re_path
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
    path('resources/', include('resources.urls')),

    # ZKTeco ADMS endpoints at root level (device pushes to /iclock/...)
    path('iclock/cdata', iclock_cdata, name='iclock_cdata_root'),
    path('iclock/getrequest', iclock_getrequest, name='iclock_getrequest_root'),
    path('iclock/devicecmd', iclock_devicecmd, name='iclock_devicecmd_root'),
]

# ── Static / Media File Serving ───────────────────────────────────────────────
if settings.DEBUG:
    # Development: Django serves both static and media directly
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
else:
    # Production:
    # - STATIC files are served by WhiteNoise middleware (already in MIDDLEWARE).
    #   Do NOT add serve() for static — it blocks Django workers for every CSS/JS/image.
    # - MEDIA files should be served directly by Apache/Nginx in cPanel.
    #   If your cPanel host does NOT serve /media/ natively, uncomment only the
    #   media line below as a last resort (avoid serving static this way):
    #
    from django.views.static import serve
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]


# ── Custom Error Handlers ─────────────────────────────────────────────────────
# These are module-level assignments, not urlpatterns entries.
from dashboard.views import server_error_500  # noqa: E402
handler500 = server_error_500

