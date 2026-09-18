import os

from django.conf import settings
from django.db import models


def resource_file_upload_path(instance, filename):
    return f'resources/{instance.resource_id or "unattached"}/{filename}'


class ResourceTopic(models.Model):
    """A category in the left-hand Topics sidebar (Sales, Inventory, Billing/Accounts, ...)."""

    COLOR_CHOICES = [
        ('#f59e0b', 'Amber'),
        ('#3b82f6', 'Blue'),
        ('#10b981', 'Emerald'),
        ('#8b5cf6', 'Violet'),
        ('#ef4444', 'Red'),
        ('#0ea5e9', 'Sky'),
        ('#ec4899', 'Pink'),
        ('#64748b', 'Slate'),
    ]
    ICON_CHOICES = [
        ('fa-tags', 'Sales'),
        ('fa-headset', 'Support'),
        ('fa-boxes-stacked', 'Inventory'),
        ('fa-file-invoice-dollar', 'Billing'),
        ('fa-truck', 'Logistics'),
        ('fa-users', 'HR'),
        ('fa-gears', 'Operations'),
        ('fa-shield-halved', 'Security'),
        ('fa-chart-line', 'Reports'),
        ('fa-bullhorn', 'Marketing'),
        ('fa-laptop-code', 'IT'),
        ('fa-folder', 'General'),
    ]

    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=50, default='fa-folder')
    color = models.CharField(max_length=7, default='#6366f1')
    order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resource_topics_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    @property
    def article_count(self):
        return self.resources.count()


class Resource(models.Model):
    """A single knowledge-base article inside a Topic."""

    topic = models.ForeignKey(
        ResourceTopic,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resources',
    )
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=300, blank=True, default='', help_text='Short one-line summary shown collapsed')
    content = models.TextField(blank=True, default='', help_text='Rich text body (HTML) — use numbered lists for steps')
    notes = models.TextField(blank=True, default='', help_text='Rich text shown in the highlighted NOTES callout')
    version = models.CharField(max_length=20, blank=True, default='v1.0')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resources_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    @property
    def primary_media_type(self):
        first = self.files.first()
        return first.file_type if first else 'text'

    @property
    def file_count(self):
        return self.files.count()

    def is_read_by(self, user):
        if not user or not user.is_authenticated:
            return False
        return self.reads.filter(user=user).exists()


class ResourceFile(models.Model):
    TYPE_AUDIO = 'audio'
    TYPE_VIDEO = 'video'
    TYPE_PDF = 'pdf'
    TYPE_OTHER = 'other'
    FILE_TYPE_CHOICES = [
        (TYPE_AUDIO, 'Audio'),
        (TYPE_VIDEO, 'Video'),
        (TYPE_PDF, 'PDF'),
        (TYPE_OTHER, 'Other'),
    ]

    AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac', 'weba'}
    VIDEO_EXTENSIONS = {'mp4', 'webm', 'mov', 'avi', 'mkv', 'm4v'}
    PDF_EXTENSIONS = {'pdf'}
    ALLOWED_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | PDF_EXTENSIONS

    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='files')
    file = models.FileField(upload_to=resource_file_upload_path)
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default=TYPE_OTHER)
    original_name = models.CharField(max_length=255, blank=True, default='')
    file_size = models.PositiveBigIntegerField(default=0, help_text='Size in bytes')
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resource_files_uploaded',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return self.original_name or os.path.basename(self.file.name)

    @classmethod
    def detect_type(cls, filename):
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext in cls.AUDIO_EXTENSIONS:
            return cls.TYPE_AUDIO
        if ext in cls.VIDEO_EXTENSIONS:
            return cls.TYPE_VIDEO
        if ext in cls.PDF_EXTENSIONS:
            return cls.TYPE_PDF
        return cls.TYPE_OTHER

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = os.path.basename(self.file.name)
        if self.file and not self.file_type:
            self.file_type = self.detect_type(self.original_name or self.file.name)
        if self.file:
            try:
                self.file_size = self.file.size
            except (OSError, ValueError):
                pass
        super().save(*args, **kwargs)

    @property
    def display_size(self):
        size = self.file_size or 0
        for unit in ('B', 'KB', 'MB', 'GB'):
            if size < 1024:
                return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
            size /= 1024
        return f'{size:.1f} TB'


class ResourceRead(models.Model):
    """Tracks that a user has marked an article as read (personal tracking only)."""

    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='reads')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='resource_reads')
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('resource', 'user')]

    def __str__(self):
        return f'{self.user} read {self.resource}'
