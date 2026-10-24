import logging
import os
from dataclasses import dataclass, field

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_device import PushDevice

logger = logging.getLogger(__name__)

# Overridable so tests can point at a local stand-in for Expo
EXPO_PUSH_URL = os.getenv("EXPO_PUSH_URL", "https://exp.host/--/api/v2/push/send")
# Optional: only needed if "Enhanced push security" is enabled for the Expo project
EXPO_ACCESS_TOKEN = os.getenv("EXPO_ACCESS_TOKEN")
# Expo accepts at most 100 messages per request
BATCH_SIZE = 100


@dataclass
class PushMessage:
    to: str
    title: str
    body: str
    # Read by the app when the notification is tapped, e.g. {"url": "/clase"}
    data: dict = field(default_factory=dict)


async def send_push(db: AsyncSession, messages: list[PushMessage]) -> int:
    """Send through Expo's push service; returns how many Expo accepted.

    Tokens Expo reports as DeviceNotRegistered (app uninstalled, permission
    revoked) are deleted so they aren't tried again.
    """
    if not messages:
        return 0
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if EXPO_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {EXPO_ACCESS_TOKEN}"

    accepted = 0
    dead_tokens: list[str] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for start in range(0, len(messages), BATCH_SIZE):
            batch = messages[start:start + BATCH_SIZE]
            payload = [
                {"to": m.to, "title": m.title, "body": m.body, "data": m.data, "sound": "default"}
                for m in batch
            ]
            try:
                response = await client.post(EXPO_PUSH_URL, json=payload, headers=headers)
                response.raise_for_status()
                tickets = response.json().get("data", [])
            except (httpx.HTTPError, ValueError):
                logger.exception("Expo push request failed for %d messages", len(batch))
                continue
            # Tickets come back in the order the messages were sent
            for message, ticket in zip(batch, tickets):
                if ticket.get("status") == "ok":
                    accepted += 1
                elif (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
                    dead_tokens.append(message.to)
                else:
                    logger.warning("Expo rejected a push: %s", ticket)

    if dead_tokens:
        await db.execute(delete(PushDevice).where(PushDevice.expo_push_token.in_(dead_tokens)))
    return accepted
