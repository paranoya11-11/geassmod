
# Name: AutoReply
# Author: @GeassMod
# Version: 1.1.0
# Description: Customizable automatic replies for Heroku Userbot.

__version__ = (1, 1, 0)

# meta developer: @GeassMod

import asyncio
import time
import html

from .. import loader, utils
from herokutl.tl.custom import Message


@loader.tds
class AutoReplyMod(loader.Module):
    """Fully customizable automatic reply system."""

    strings = {
        "name": "AutoReply",
    }

    strings_ru = {
        "name": "AutoReply",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "enabled",
                False,
                "Enable AutoReply",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "reply_text",
                "Привет! Сейчас меня нет на месте. Отвечу позже!",
                "Automatic reply message (HTML supported)",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "private_only",
                True,
                "Reply only in private messages",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "reply_groups",
                False,
                "Enable replies in groups",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "delete_on_reply",
                True,
                "Delete previous auto reply when user messages again",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "ignore_contacts",
                False,
                "Ignore contacts",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "ignore_bots",
                True,
                "Ignore bots",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "ignore_forwarded",
                False,
                "Ignore forwarded messages",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "delay",
                1,
                "Reply delay in seconds",
                validator=loader.validators.Integer(
                    minimum=0,
                    maximum=60,
                ),
            ),
            loader.ConfigValue(
                "cooldown",
                60,
                "Cooldown in minutes",
                validator=loader.validators.Integer(
                    minimum=0,
                    maximum=10080,
                ),
            ),
            loader.ConfigValue(
                "ignore_ids",
                [],
                "User IDs to ignore",
                validator=loader.validators.Series(
                    loader.validators.Integer()
                ),
            ),
            loader.ConfigValue(
                "allow_ids",
                [],
                "Allowed user IDs (empty = everyone)",
                validator=loader.validators.Series(
                    loader.validators.Integer()
                ),
            ),
        )

    async def client_ready(self):
        self._cooldowns = self.get("cooldowns", {})
        self._auto_replies = self.get("auto_replies", {})

        if not isinstance(self._cooldowns, dict):
            self._cooldowns = {}

        if not isinstance(self._auto_replies, dict):
            self._auto_replies = {}

    def _save_data(self):
        self.set("cooldowns", self._cooldowns)
        self.set("auto_replies", self._auto_replies)

    def _make_key(self, chat_id, sender_id):
        return f"{chat_id}:{sender_id}"

    def _render(self, sender):
        """Render placeholders in the reply."""

        text = self.config["reply_text"]

        username = getattr(sender, "username", None)

        values = {
            "{name}": getattr(sender, "first_name", "") or "",
            "{first_name}": getattr(sender, "first_name", "") or "",
            "{last_name}": getattr(sender, "last_name", "") or "",
            "{username}": f"@{username}" if username else "",
            "{id}": str(sender.id),
        }

        for key, value in values.items():
            text = text.replace(key, html.escape(str(value)))

        return text

    async def _delete_previous(self, message):
        """Delete the previous automatic reply in this chat."""

        if not self.config["delete_on_reply"]:
            return

        key = self._make_key(
            message.chat_id,
            message.sender_id,
        )

        data = self._auto_replies.get(key)

        if not data:
            return

        try:
            await self.client.delete_messages(
                data["chat_id"],
                data["message_id"],
            )

            self._auto_replies.pop(key, None)
            self._save_data()

        except Exception:
            pass

    # -------------------------
    # COMMANDS
    # -------------------------

    @loader.command(ru_doc="Включить автоответчик")
    async def aron(self, message: Message):
        """Enable AutoReply."""

        self.config["enabled"] = True

        await utils.answer(
            message,
            "🟢 <b>AutoReply включён.</b>",
        )

    @loader.command(ru_doc="Выключить автоответчик")
    async def aroff(self, message: Message):
        """Disable AutoReply."""

        self.config["enabled"] = False

        await utils.answer(
            message,
            "🔴 <b>AutoReply выключен.</b>",
        )

    @loader.command(ru_doc="Изменить сообщение автоответчика")
    async def artext(self, message: Message):
        """Change automatic reply text."""

        text = utils.get_args_raw(message)

        if not text:
            await utils.answer(
                message,
                "❌ Укажи текст.\n\n"
                "<code>.artext Новый текст</code>",
            )
            return

        self.config["reply_text"] = text

        await utils.answer(
            message,
            "✅ <b>Текст автоответа обновлён.</b>",
        )

    @loader.command(ru_doc="Предпросмотр автоответа")
    async def arpreview(self, message: Message):
        """Preview automatic reply."""

        sender = await message.get_sender()

        if not sender:
            await utils.answer(
                message,
                "❌ Не удалось получить пользователя.",
            )
            return

        await utils.answer(
            message,
            "<b>AutoReply Preview:</b>\n\n"
            + self._render(sender),
        )

    @loader.command(ru_doc="Показать настройки автоответчика")
    async def arstatus(self, message: Message):
        """Show AutoReply status."""

        enabled = self.config["enabled"]

        text = (
            "<b>GEASS MODULES | AutoReply</b>\n\n"
            f"<b>Status:</b> {'🟢 ON' if enabled else '🔴 OFF'}\n"
            f"<b>Private only:</b> {self.config['private_only']}\n"
            f"<b>Groups:</b> {self.config['reply_groups']}\n"
            f"<b>Delete previous reply:</b> "
            f"{self.config['delete_on_reply']}\n"
            f"<b>Ignore contacts:</b> {self.config['ignore_contacts']}\n"
            f"<b>Ignore bots:</b> {self.config['ignore_bots']}\n"
            f"<b>Delay:</b> {self.config['delay']} sec.\n"
            f"<b>Cooldown:</b> {self.config['cooldown']} min."
        )

        await utils.answer(message, text)

    @loader.command(ru_doc="Очистить историю автоответчика")
    async def arclear(self, message: Message):
        """Clear cooldown and reply history."""

        self._cooldowns = {}
        self._auto_replies = {}

        self._save_data()

        await utils.answer(
            message,
            "✅ <b>История AutoReply очищена.</b>",
        )

    # -------------------------
    # MESSAGE WATCHER
    # -------------------------

    @loader.watcher(no_commands=True)
    async def autoreply_watcher(self, message: Message):
        """Handle incoming messages and automatic replies."""

        if message.out or not message.sender_id:
            return

        if not getattr(message, "message", None):
            return

        sender_id = message.sender_id
        chat_id = message.chat_id

        # Delete the previous automatic reply first.
        # This works even if AutoReply has been disabled.
        await self._delete_previous(message)

        if not self.config["enabled"]:
            return

        # Private / group restrictions.
        if message.is_private:
            pass
        else:
            if self.config["private_only"]:
                return

            if not self.config["reply_groups"]:
                return

        # Ignore forwarded messages.
        if (
            self.config["ignore_forwarded"]
            and getattr(message, "fwd_from", None)
        ):
            return

        # Ignore blacklist.
        if sender_id in self.config["ignore_ids"]:
            return

        # Whitelist.
        allowed = self.config["allow_ids"]

        if allowed and sender_id not in allowed:
            return

        try:
            sender = await message.get_sender()

            if not sender:
                return

            # Ignore bots.
            if (
                self.config["ignore_bots"]
                and getattr(sender, "bot", False)
            ):
                return

            # Ignore contacts.
            if (
                self.config["ignore_contacts"]
                and getattr(sender, "contact", False)
            ):
                return

            key = self._make_key(chat_id, sender_id)

            # Cooldown.
            now = time.time()
            last_reply = self._cooldowns.get(key, 0)

            cooldown = self.config["cooldown"] * 60

            if cooldown and now - last_reply < cooldown:
                return

            # Delay.
            delay = self.config["delay"]

            if delay:
                await asyncio.sleep(delay)

            # Check settings again after delay.
            if not self.config["enabled"]:
                return

            # Build reply.
            reply = self._render(sender)

            if not reply.strip():
                return

            # Send reply.
            sent = await message.reply(reply)

            # Save message information.
            self._auto_replies[key] = {
                "chat_id": chat_id,
                "message_id": sent.id,
            }

            self._cooldowns[key] = time.time()

            self._save_data()

        except Exception:
            return
