from django.db import models
from django.conf import settings
from django.utils import timezone


class ChatThread(models.Model):
    """Represents a conversation between users (1-on-1 or group)"""
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='chat_threads'
    )
    is_group = models.BooleanField(default=False)
    group_name = models.CharField(max_length=100, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_threads'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        if self.is_group and self.group_name:
            return f"Group: {self.group_name}"
        usernames = ', '.join(p.username for p in self.participants.all())
        return f"Thread: {usernames}"

    def get_other_participant(self, user):
        """For 1-on-1 threads, return the other user"""
        if self.is_group:
            return None
        return self.participants.exclude(id=user.id).first()

    def get_display_name(self, user):
        """Get display name for the thread"""
        if self.is_group:
            return self.group_name or 'Unnamed Group'
        other = self.get_other_participant(user)
        if other:
            return other.get_full_name() or other.username
        return 'Unknown'

    def get_last_message(self):
        return self.messages.order_by('-created_at').first()

    def get_unread_count(self, user):
        return self.messages.filter(is_read=False).exclude(sender=user).count()


class ChatMessage(models.Model):
    """Individual message within a thread"""
    thread = models.ForeignKey(
        ChatThread,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages'
    )
    content = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username}: {self.content[:50]}"
