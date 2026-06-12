from django.contrib.sessions.exceptions import SessionInterrupted
from django.http import HttpResponseRedirect, JsonResponse
from django.conf import settings

class GracefulSessionInterruptionMiddleware:
    """
    Catches the SessionInterrupted exception raised by SessionMiddleware
    when SESSION_SAVE_EVERY_REQUEST is True and a session is deleted concurrently.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
            return response
        except SessionInterrupted:
            # If the request is an API call or AJAX, return JSON
            if request.path.startswith('/api/') or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                response = JsonResponse({'error': 'Session interrupted. Please log in again.'}, status=401)
            else:
                # Otherwise redirect to login
                login_url = getattr(settings, 'LOGIN_URL', '/login/')
                response = HttpResponseRedirect(login_url)
            
            # Clean up the session cookie just in case
            cookie_name = getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid')
            response.delete_cookie(cookie_name)
            return response
