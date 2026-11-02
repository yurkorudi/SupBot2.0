from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def alert_keyboard(alert_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Займаюсь цим", callback_data=f"ack:{alert_id}")],
            [InlineKeyboardButton(text="➕ Зробити SWAP +1 GB", callback_data=f"swap:{alert_id}")],
        ]
    )
