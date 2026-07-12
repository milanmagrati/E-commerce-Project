from django.db import models
from django.conf import settings
from django.utils import timezone


class CRMContact(models.Model):
    LEAD_STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('qualified', 'Qualified'),
        ('converted', 'Converted'),
        ('lost', 'Lost'),
    ]
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    company = models.CharField(max_length=200, blank=True, null=True)
    meta_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=LEAD_STATUS_CHOICES, default='new')
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


class CRMConversation(models.Model):
    CHANNEL_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('instagram', 'Instagram'),
        ('facebook', 'Facebook'),
        ('email', 'Email'),
        ('sms', 'SMS'),
        ('web', 'Web Chat'),
    ]
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('resolved', 'Resolved'),
        ('pending', 'Pending'),
    ]
    contact = models.ForeignKey(CRMContact, on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations')
    integration = models.ForeignKey('CRMIntegration', on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations')
    account_id = models.CharField(max_length=255, blank=True, null=True)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default='web')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    subject = models.CharField(max_length=300, blank=True, null=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(default=timezone.now)
    last_message = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Conversation #{self.pk}"

    class Meta:
        ordering = ['-updated_at']


class CRMMessage(models.Model):
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
        ('failed', 'Failed'),
    ]
    conversation = models.ForeignKey(CRMConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=100)
    body = models.TextField()
    is_outbound = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='sent')
    created_at = models.DateTimeField(default=timezone.now)

    @property
    def display_date(self):
        now = timezone.now().date()
        if timezone.is_aware(self.created_at):
            msg_date = timezone.localtime(self.created_at).date()
        else:
            msg_date = self.created_at.date()
            
        delta = now - msg_date
        if delta.days == 0:
            return "Today"
        elif delta.days == 1:
            return "Yesterday"
        else:
            return msg_date.strftime("%b %d, %Y")

    class Meta:
        ordering = ['created_at']


class CRMQuickReply(models.Model):
    name = models.CharField(max_length=100)
    shortcut = models.CharField(max_length=50, unique=True)
    content = models.TextField()
    category = models.CharField(max_length=100, blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class CRMChatbotConfig(models.Model):
    name = models.CharField(max_length=200, default='Trendy AI')
    is_active = models.BooleanField(default=False)
    business_name = models.CharField(max_length=200, blank=True, null=True)
    business_email = models.EmailField(blank=True, null=True)
    business_phone = models.CharField(max_length=30, blank=True, null=True)
    about_blurb = models.TextField(blank=True, null=True)
    welcome_message = models.TextField(blank=True, null=True)
    auto_reply_channels = models.JSONField(default=dict)
    ai_credits = models.IntegerField(default=100)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Chatbot Configuration'


class CRMIntegration(models.Model):
    CHANNEL_TYPE_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('instagram', 'Instagram'),
        ('facebook', 'Facebook Messenger'),
        ('tiktok', 'TikTok'),
        ('gmail', 'Gmail'),
        ('outlook', 'Outlook'),
        ('zoho_mail', 'Zoho Mail'),
    ]
    STATUS_CHOICES = [
        ('connected', 'Connected'),
        ('not_connected', 'Not Connected'),
        ('error', 'Error'),
    ]
    channel_type = models.CharField(max_length=30, choices=CHANNEL_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='not_connected')
    account_name = models.CharField(max_length=200, blank=True, null=True)
    access_token = models.TextField(blank=True, null=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    meta = models.JSONField(default=dict)

    @property
    def parsed_name(self):
        import re
        if self.account_name:
            match = re.match(r'^(.*?) \((\d+)\)$', self.account_name.strip())
            if match:
                return match.group(1).strip()
        return self.account_name

    @property
    def parsed_id(self):
        import re
        if self.account_name:
            match = re.match(r'^(.*?) \((\d+)\)$', self.account_name.strip())
            if match:
                return match.group(2)
        return ''

    def __str__(self):
        return f"{self.get_channel_type_display()} - {self.status}"

    class Meta:
        ordering = ['channel_type']


class CRMTicket(models.Model):
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, null=True)
    contact = models.ForeignKey(CRMContact, on_delete=models.SET_NULL, null=True, blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"#{self.pk} - {self.title}"

    class Meta:
        ordering = ['-created_at']


class CRMSocialPost(models.Model):
    integration = models.ForeignKey(CRMIntegration, on_delete=models.CASCADE, related_name='social_posts')
    meta_post_id = models.CharField(max_length=255, unique=True)
    message = models.TextField(blank=True, null=True)
    picture_url = models.URLField(max_length=1000, blank=True, null=True)
    created_time = models.DateTimeField(null=True, blank=True)
    likes_count = models.IntegerField(default=0)
    comments_count = models.IntegerField(default=0)
    is_starred = models.BooleanField(default=False)
    is_reviewed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Post {self.meta_post_id}"

    class Meta:
        ordering = ['-created_time']


class CRMSocialComment(models.Model):
    VISIBILITY_CHOICES = [
        ('visible', 'Visible'),
        ('hidden', 'Hidden'),
        ('spam', 'Spam'),
    ]
    WORKFLOW_CHOICES = [
        ('open', 'Open'),
        ('done', 'Done'),
    ]
    post = models.ForeignKey(CRMSocialPost, on_delete=models.CASCADE, related_name='comments')
    meta_comment_id = models.CharField(max_length=255, unique=True)
    parent_comment = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    sender_name = models.CharField(max_length=200)
    sender_id = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField()
    created_time = models.DateTimeField(null=True, blank=True)
    like_count = models.IntegerField(default=0)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    workflow_status = models.CharField(max_length=20, choices=WORKFLOW_CHOICES, default='open')
    visibility_status = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='visible')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Comment by {self.sender_name}"

    class Meta:
        ordering = ['-created_time']
