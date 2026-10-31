"""Where an SMS or voice provider would plug in.

No provider is connected. One needs a vendor agreement, consent and a security
review first, so a real adapter must be registered deliberately in code. There is
no fallback: an unavailable channel means a person follows up.
"""
from typing import Dict, List, Protocol

from backend.config import settings

# Fixed wording: an outside carrier must never see anything clinical.
NEUTRAL_MESSAGE = "Please contact your clinic. This is a message from your care team."


class ChannelUnavailable(Exception):
    """This channel can't be used here."""


class DeliveryFailed(Exception):
    """The provider refused or lost the message."""


class Adapter(Protocol):
    def send(self, phone: str, message: str) -> str:
        """Hands a message to the provider and returns its reference. This is not delivery."""


_adapters: Dict[str, Adapter] = {}


def register(channel: str, adapter: Adapter) -> None:
    _adapters[channel] = adapter


def clear() -> None:
    _adapters.clear()


def configured_channels() -> List[str]:
    return sorted(_adapters) if settings.enable_external_messaging else []


def send(channel: str, phone: str) -> str:
    if channel not in configured_channels():
        raise ChannelUnavailable(f"{channel} isn't configured")
    return _adapters[channel].send(phone, NEUTRAL_MESSAGE)
