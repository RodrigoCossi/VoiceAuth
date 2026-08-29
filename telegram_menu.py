"""Telegram bot-command menu (the "/" autocomplete list in the client).

Registering commands via set_my_commands is what makes them appear as
suggestions when the user types "/" in Telegram — without it, commands still
work if typed manually, but the client shows no hints. Two menus are kept,
scoped to the authorized user's chat only, and swapped as their session state
changes. The global/default scope is left empty so a stranger who opens a
chat with the bot sees no command hints at all (matches Gate 1's "reveal
nothing" behavior).
"""

from __future__ import annotations

import logging

from telegram import BotCommand, BotCommandScopeChat
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

LOCKED_COMMANDS = [
    BotCommand("pin", "Authenticate with your PIN"),
    BotCommand("enroll", "Enroll your voiceprint (first-time only)"),
    BotCommand("lock", "Confirm session is locked"),
]

UNLOCKED_COMMANDS = [
    BotCommand("status", "Show system status"),
    BotCommand("help", "List available commands"),
    BotCommand("auth_log", "Show last 10 authentication attempts"),
    BotCommand("lock", "End the current session"),
]


async def set_command_menu(bot, chat_id: int, authenticated: bool) -> None:
    commands = UNLOCKED_COMMANDS if authenticated else LOCKED_COMMANDS
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id))
    except TelegramError as exc:
        logger.warning("Could not update command menu: %s", exc)
