# Minimal stubs for telethon to satisfy mypy
# This is not a complete stub file, just enough to reduce mypy errors

from typing import Any, Optional, Union, List, Dict, AsyncGenerator

# Basic types
class Message:
    id: int
    date: Any
    message: Optional[str]
    media: Optional[Any]
    file: Optional[Any]
    action: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class User:
    id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    def __init__(self, *args, **kwargs) -> None: ...

class Channel:
    id: int
    title: str
    username: Optional[str]
    forum: bool
    megagroup: bool
    def __init__(self, *args, **kwargs) -> None: ...

class Chat:
    id: int
    title: str
    def __init__(self, *args, **kwargs) -> None: ...

class ForumTopic:
    id: int
    title: str
    icon_emoji_id: Optional[int]
    date: Optional[Any]
    closed: bool
    pinned: bool
    def __init__(self, *args, **kwargs) -> None: ...

# Media types
class MessageMediaPhoto:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaDocument:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaWebPage:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaPoll:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaDice:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaGame:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaVenue:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaGeo:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaGeoLive:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaContact:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaInvoice:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaPaidMedia:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaStory:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaGiveaway:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaGiveawayResults:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaToDo:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaUnsupported:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class MessageMediaEmpty:
    document: Optional[Any]
    def __init__(self, *args, **kwargs) -> None: ...

class DocumentEmpty:
    attributes: Optional[List[Any]]
    def __init__(self, *args, **kwargs) -> None: ...

class DocumentAttributeImageSize:
    file_name: Optional[str]
    def __init__(self, *args, **kwargs) -> None: ...

class DocumentAttributeCustomEmoji:
    file_name: Optional[str]
    def __init__(self, *args, **kwargs) -> None: ...

class DocumentAttributeHasStickers:
    file_name: Optional[str]
    def __init__(self, *args, **kwargs) -> None: ...

class DocumentAttributeAudio:
    duration: Optional[float]
    title: Optional[str]
    performer: Optional[str]
    voice: bool
    file_name: Optional[str]
    def __init__(self, *args, **kwargs) -> None: ...

class DocumentAttributeVideo:
    duration: Optional[float]
    w: Optional[int]
    h: Optional[int]
    file_name: Optional[str]
    def __init__(self, *args, **kwargs) -> None: ...

class DocumentAttributeSticker:
    file_name: Optional[str]
    def __init__(self, *args, **kwargs) -> None: ...

class DocumentAttributeAnimated:
    file_name: Optional[str]
    def __init__(self, *args, **kwargs) -> None: ...

# Functions
class InvokeWithTakeoutRequest:
    def __init__(self, *args, **kwargs) -> None: ...

class InitTakeoutSessionRequest:
    def __init__(self, *args, **kwargs) -> None: ...

class FinishTakeoutSessionRequest:
    def __init__(self, *args, **kwargs) -> None: ...

class GetHistoryRequest:
    def __init__(self, *args, **kwargs) -> None: ...

class GetForumTopicsRequest:
    def __init__(self, *args, **kwargs) -> None: ...

# Client
class TelegramClient:
    _takeout_id: Optional[int]
    def __init__(self, *args, **kwargs) -> None: ...
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_user_authorized(self) -> bool: ...
    async def get_me(self) -> Optional[User]: ...
    async def send_code_request(self, phone: str) -> Any: ...
    async def sign_in(self, phone: Optional[str] = None, code: Optional[str] = None, password: Optional[str] = None) -> Any: ...
    async def get_entity(self, entity: Union[str, int]) -> Any: ...
    async def get_dialogs(self, limit: Optional[int] = None, offset_date: Optional[Any] = None, offset_id: int = 0, offset_peer: Optional[Any] = None) -> List[Any]: ...
    async def start(self) -> None: ...
    async def get_messages(self, *args, **kwargs) -> Any: ...
    async def download_media(self, *args, **kwargs) -> Optional[bytes]: ...
    def iter_messages(self, *args, **kwargs) -> AsyncGenerator[Message, None]: ...
    async def __call__(self, request: Any) -> Any: ...

class TelegramManager:
    client: TelegramClient
    _external_takeout_id: Optional[int]
    def __init__(self, *args, **kwargs) -> None: ...
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_connected(self) -> bool: ...
    async def get_entity(self, entity: Union[str, int]) -> Any: ...
    def iter_messages(self, *args, **kwargs) -> AsyncGenerator[Message, None]: ...
    async def fetch_messages(self, *args, **kwargs) -> AsyncGenerator[Message, None]: ...
    def get_worker_stats(self) -> Dict[str, Any]: ...

# Utils
class utils:
    @staticmethod
    def get_display_name(entity: Any) -> str: ...
    @staticmethod
    def get_input_location(media: Any) -> Any: ...

# Types
class types:
    Message = Message
    User = User
    Channel = Channel
    Chat = Chat
    ForumTopic = ForumTopic

# Functions module
class functions:
    messages = Any
    account = Any
    channels = Any

# Errors
class FloodWaitError(Exception):
    seconds: int
    def __init__(self, seconds: int) -> None: ...

class RPCError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class SlowModeWaitError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class ChannelPrivateError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class ChatAdminRequiredError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class PeerIdInvalidError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class SessionPasswordNeededError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class UsernameInvalidError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class UsernameNotOccupiedError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class UserNotParticipantError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...

class TimeoutError(Exception):
    def __init__(self, *args, **kwargs) -> None: ...