import json
from channels.generic.websocket import AsyncWebsocketConsumer

class FollowUpConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # We'll use a single group for all follow-up page viewers
        self.group_name = 'follow_ups_group'
        
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        data = json.loads(text_data)
        event_type = data.get('type')
        
        if event_type in ['typing', 'stopped_typing', 'viewing']:
            # Broadcast the presence event to everyone else
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'presence_event',
                    'event_type': event_type,
                    'user': self.scope["user"].username if self.scope["user"].is_authenticated else "Anonymous",
                    'id': data.get('id'),
                    'sender_channel_name': self.channel_name
                }
            )

    async def presence_event(self, event):
        # Don't send back to the user who sent it
        if self.channel_name != event.get('sender_channel_name'):
            await self.send(text_data=json.dumps({
                'type': event['event_type'],
                'user': event['user'],
                'id': event['id']
            }))

    async def row_updated(self, event):
        # This will be called from views.py when a row is saved
        await self.send(text_data=json.dumps({
            'type': 'row_updated',
            'id': event['id'],
            'data': event['data']
        }))
