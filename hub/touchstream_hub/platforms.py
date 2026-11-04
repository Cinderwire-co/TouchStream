"""Streaming platforms the dashboard knows how to set up

YouTube only for now. Each entry pre-fills the server address and explains
where to find the stream key; restreaming itself works with any RTMP(S) server.
"""

PLATFORMS = [
    {
        'id': 'youtube', 'name': 'YouTube',
        'url': 'rtmps://a.rtmps.youtube.com/live2',
        'link': 'https://studio.youtube.com',
        'steps': ['Open YouTube Studio and choose Create → Go live.',
                  'Pick Stream (not Webcam) and copy the Stream key.',
                  'Paste it below. YouTube starts the broadcast when video arrives, '
                  'or when you press Go live in Studio if auto-start is off.'],
    },
]

BY_ID = {p['id']: p for p in PLATFORMS}
