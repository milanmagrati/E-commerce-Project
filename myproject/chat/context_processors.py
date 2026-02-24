def unread_message_count(request):
    """Provide unread message count globally for navbar badge"""
    if not request.user.is_authenticated:
        return {}

    from chat.models import ChatMessage

    count = ChatMessage.objects.filter(
        thread__participants=request.user,
        is_read=False
    ).exclude(
        sender=request.user
    ).count()

    return {
        'unread_chat_count': count,
    }
