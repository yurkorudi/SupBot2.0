import asyncio
import logging
from contextlib import suppress
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from app import create_app
from app.extensions import db
from app.models import Alert, Outbox, utcnow

from .handlers.commands import router
from .keyboards.alerts import alert_keyboard
from .services.backend import Backend

logger = logging.getLogger(__name__)


def claim_messages():
    now = utcnow()
    rows = db.session.scalars(
        db.select(Outbox)
        .where(Outbox.sent_at.is_(None), Outbox.available_at <= now)
        .order_by(Outbox.id)
        .limit(10)
        .with_for_update(skip_locked=True)
    ).all()
    result = []
    for row in rows:
        row.attempts += 1
        row.available_at = now + timedelta(minutes=5)
        alert = db.session.get(Alert, row.alert_id) if row.alert_id else None
        critical = bool(alert and not alert.resolved_at and alert.severity == "critical")
        result.append(
            {
                "id": row.id,
                "group": row.group_id,
                "text": row.message,
                "alert_id": row.alert_id if critical else None,
                "allow_swap": bool(critical and alert.alert_type == "ram"),
                "attempts": row.attempts,
                "edit_message_id": row.edit_message_id,
            }
        )
    db.session.commit()
    return result


def mark_message(identifier, sent, delay=0):
    row = db.session.get(Outbox, identifier)
    if sent:
        row.sent_at = utcnow()
    else:
        row.available_at = utcnow() + timedelta(seconds=delay)
    db.session.commit()


async def deliver(bot, backend):
    while True:
        try:
            messages = await backend.run(claim_messages)
            for item in messages:
                try:
                    await send_item(bot, item)
                    await backend.run(mark_message, item["id"], True)
                except TelegramRetryAfter as error:
                    await backend.run(mark_message, item["id"], False, error.retry_after + 1)
                except Exception as error:
                    logger.warning("Telegram delivery id=%s: %s", item["id"], type(error).__name__)
                    await backend.run(
                        mark_message,
                        item["id"],
                        False,
                        min(3600, 15 * 2 ** min(item["attempts"], 8)),
                    )
        except Exception as error:
            logger.error("Черга Telegram: %s", type(error).__name__)
        await asyncio.sleep(3)


async def send_item(bot, item):
    if item["edit_message_id"]:
        try:
            await bot.edit_message_text(
                text=item["text"],
                chat_id=item["group"],
                message_id=item["edit_message_id"],
            )
            return
        except TelegramBadRequest as error:
            if "message is not modified" in error.message.lower():
                return
    await bot.send_message(
        item["group"],
        item["text"],
        reply_markup=(
            alert_keyboard(item["alert_id"], item["allow_swap"]) if item["alert_id"] else None
        ),
    )


async def main():
    app = create_app()
    if not app.config["TELEGRAM_BOT_TOKEN"] or not app.config["TELEGRAM_ADMIN_ID"]:
        raise RuntimeError("Задайте TELEGRAM_BOT_TOKEN і числовий TELEGRAM_ADMIN_ID.")
    backend = Backend(app)
    bot = Bot(
        app.config["TELEGRAM_BOT_TOKEN"], default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dispatcher = Dispatcher(backend=backend)
    dispatcher.include_router(router)
    delivery = asyncio.create_task(deliver(bot, backend))
    try:
        await dispatcher.start_polling(bot, handle_as_tasks=False)
    finally:
        delivery.cancel()
        with suppress(asyncio.CancelledError):
            await delivery
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
