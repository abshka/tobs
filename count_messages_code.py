from telethon import TelegramClient
from telethon.errors.rpc_errors_401 import SessionPasswordNeededError

# (1) Use your own values here
api_id = 17349
api_hash = "344583e45741c457fe1862106095a5eb"

phone = "YOUR_NUMBER_HERE"
username = "username"

# (2) Create the client and connect
client = TelegramClient(username, api_id, api_hash)
client.connect()

# Ensure you're authorized
if not client.is_user_authorized():
    client.send_code_request(phone)
    try:
        client.sign_in(phone, input("Enter the code: "))
    except SessionPasswordNeededError:
        client.sign_in(password=input("Password: "))

me = client.get_me()
print(me)

from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

get_dialogs = GetDialogsRequest(
    offset_date=None,
    offset_id=0,
    offset_peer=InputPeerEmpty(),
    limit=30,
)

dialogs = client(get_dialogs)
print(dialogs)

from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import (
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
    PeerChannel,
    PeerChat,
    PeerUser,
)
from telethon.tl.types.messages import Messages

counts = {}

# create dictionary of ids to users and chats
users = {}
chats = {}

for u in dialogs.users:
    users[u.id] = u

for c in dialogs.chats:
    chats[c.id] = c

for d in dialogs.dialogs:
    peer = d.peer
    if isinstance(peer, PeerChannel):
        id = peer.channel_id
        channel = chats[id]
        access_hash = channel.access_hash
        name = channel.title

        input_peer = InputPeerChannel(id, access_hash)
    elif isinstance(peer, PeerChat):
        id = peer.chat_id
        group = chats[id]
        name = group.title

        input_peer = InputPeerChat(id)
    elif isinstance(peer, PeerUser):
        id = peer.user_id
        user = users[id]
        access_hash = user.access_hash
        name = user.first_name

        input_peer = InputPeerUser(id, access_hash)
    else:
        continue

    get_history = GetHistoryRequest(
        peer=input_peer,
        offset_id=0,
        offset_date=None,
        add_offset=0,
        limit=1,
        max_id=0,
        min_id=0,
    )

    history = client(get_history)
    if isinstance(history, Messages):
        count = len(history.messages)
    else:
        count = history.count

    counts[name] = count

print(counts)
sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
for name, count in sorted_counts:
    print("{}: {}".format(name, count))

# Using helper methods

# from telethon.tl.types import User
#
# _, entities = client.get_dialogs(limit=30)
#
# counts = []
# for e in entities:
#     if isinstance(e, User):
#         name = e.first_name
#     else:
#         name = e.title
#
#     count, _, _ = client.get_message_history(e, limit=1)
#     counts.append((name, count))
#
# message_counts.sort(key=lambda x: x[1], reverse=True)
# for name, count in counts:
#     print('{}: {}'.format(name, count))
