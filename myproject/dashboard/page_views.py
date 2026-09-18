from html.parser import HTMLParser

from django.db import IntegrityError
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils.text import slugify
from store.models import Page

# Django's own field limits - kept in sync with store.models.Page so we can
# validate/truncate before hitting the DB instead of letting MySQL raise a
# "Data too long for column" error (strict mode does not silently truncate).
_TITLE_MAX_LENGTH = Page._meta.get_field('title').max_length
_SLUG_MAX_LENGTH = Page._meta.get_field('slug').max_length

# Tags that only exist to wrap a full HTML document; unwrapping them (but
# keeping everything they contain, e.g. <style>/<script> from <head>) lets a
# pasted full document and a pasted body fragment behave identically.
_DOCUMENT_WRAPPER_TAGS = {'html', 'head', 'body'}


class _DocumentUnwrapper(HTMLParser):
    """Removes only <html>/<head>/<body> (and <!DOCTYPE>) tags from a chunk of
    HTML, preserving every other tag, attribute, comment and text node -
    including raw <style>/<script> bodies - exactly as written and in order.

    A real parser (vs. a regex) is used so this doesn't break on things a
    naive `<[^>]*>` pattern gets wrong, e.g. a `>` inside a quoted attribute
    (`<body onload="if (1>0) {}">`) or badly nested/unclosed tags.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag not in _DOCUMENT_WRAPPER_TAGS:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if tag not in _DOCUMENT_WRAPPER_TAGS:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag not in _DOCUMENT_WRAPPER_TAGS:
            self.parts.append('</%s>' % tag)

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append('&%s;' % name)

    def handle_charref(self, name):
        self.parts.append('&#%s;' % name)

    def handle_comment(self, data):
        self.parts.append('<!--%s-->' % data)

    def handle_decl(self, decl):
        pass  # drops <!DOCTYPE html>

    def unknown_decl(self, data):
        pass


def normalize_page_content(content):
    """Strip full-document wrapper tags so pasted content always renders as a
    plain fragment inside the store/admin layout, regardless of whether the
    user (or an AI) pasted a full <html> document or just the body content."""
    if not content:
        return content
    unwrapper = _DocumentUnwrapper()
    unwrapper.feed(content)
    unwrapper.close()
    return ''.join(unwrapper.parts).strip()


def _clean_page_fields(request):
    """Validate/normalize title, slug and content from a page form POST.

    Returns (title, slug, content, error) - error is a user-facing message
    if the submission can't be saved, otherwise None. Doing this here means
    an admin's save can never crash with a raw DB error (e.g. MySQL's
    'Data too long for column' in strict mode, or an invalid slug that the
    /p/<slug>/ URL pattern can never match) - it either succeeds or fails
    with a message, same as before the extra validation existed.
    """
    title = (request.POST.get('title') or '').strip()[:_TITLE_MAX_LENGTH]
    content = normalize_page_content(request.POST.get('content'))

    raw_slug = (request.POST.get('slug') or '').strip()
    slug = slugify(raw_slug)[:_SLUG_MAX_LENGTH] if raw_slug else ''
    if not slug:
        # Fall back to deriving it from the title so a slug typed with only
        # unsupported characters (e.g. non-Latin text) doesn't silently save
        # as an empty/invalid slug that the public URL can never match.
        slug = slugify(title)[:_SLUG_MAX_LENGTH]

    if not title:
        return title, slug, content, 'Page title is required.'
    if not slug:
        return title, slug, content, 'Could not derive a valid URL slug from the title - please set one manually using letters, numbers and hyphens.'
    if not content:
        return title, slug, content, 'Page content is required.'

    return title, slug, content, None


@login_required(login_url='login')
def page_list(request):
    # Only admins or superusers should access this ideally
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, 'Permission Denied')
        return redirect('dashboard')

    pages = Page.objects.all().order_by('-created_at')
    return render(request, 'dashboard/pages/page_list.html', {'pages': pages})

@login_required(login_url='login')
def page_create(request):
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, 'Permission Denied')
        return redirect('dashboard')

    if request.method == 'POST':
        title, slug, content, error = _clean_page_fields(request)

        if error:
            messages.error(request, error)
        elif Page.objects.filter(slug=slug).exists():
            messages.error(request, 'A page with this slug already exists.')
        else:
            try:
                Page.objects.create(
                    title=title,
                    slug=slug,
                    content=content,
                    is_published=request.POST.get('is_published') == 'on'
                )
            except IntegrityError:
                messages.error(request, 'A page with this slug already exists.')
            else:
                messages.success(request, 'Page created successfully.')
                return redirect('page_list')

    return render(request, 'dashboard/pages/page_form.html')

@login_required(login_url='login')
def page_edit(request, pk):
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, 'Permission Denied')
        return redirect('dashboard')

    page = get_object_or_404(Page, pk=pk)
    if request.method == 'POST':
        title, slug, content, error = _clean_page_fields(request)

        # Reflect what was just typed back into the form even on error/re-render,
        # so a validation failure doesn't silently discard the admin's edits.
        page.title = title
        page.slug = slug
        page.content = content
        page.is_published = request.POST.get('is_published') == 'on'

        if error:
            messages.error(request, error)
        elif Page.objects.filter(slug=slug).exclude(pk=page.pk).exists():
            messages.error(request, 'A page with this slug already exists.')
        else:
            try:
                page.save()
            except IntegrityError:
                messages.error(request, 'A page with this slug already exists.')
            else:
                messages.success(request, 'Page updated successfully.')
                return redirect('page_list')

    return render(request, 'dashboard/pages/page_form.html', {'page': page})

@login_required(login_url='login')
def page_delete(request, pk):
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, 'Permission Denied')
        return redirect('dashboard')

    page = get_object_or_404(Page, pk=pk)
    if request.method == 'POST':
        page.delete()
        messages.success(request, 'Page deleted successfully.')
        return redirect('page_list')
    return redirect('page_list')
