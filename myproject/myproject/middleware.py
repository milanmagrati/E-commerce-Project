from django.contrib.sessions.exceptions import SessionInterrupted
from django.http import HttpResponseRedirect, JsonResponse
from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware

class GracefulSessionInterruptionMiddleware(SessionMiddleware):
    """
    Overrides SessionMiddleware to gracefully handle SessionInterrupted exceptions
    when SESSION_SAVE_EVERY_REQUEST is True and a session is deleted concurrently.
    """
    def process_response(self, request, response):
        try:
            return super().process_response(request, response)
        except SessionInterrupted:
            # If the request is an API call or AJAX, return JSON
            if request.path.startswith('/api/') or request.path.startswith('/chat/api/') or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                response = JsonResponse({'error': 'Session interrupted. Please log in again.'}, status=401)
            else:
                # Otherwise redirect to login
                login_url = getattr(settings, 'LOGIN_URL', '/login/')
                response = HttpResponseRedirect(login_url)
            
            # Clean up the session cookie just in case
            cookie_name = getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid')
            response.delete_cookie(cookie_name)
            return response
