from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def alert_keyboard(alert_id, allow_swap=False):
    buttons = [[InlineKeyboardButton(text="🛠 Займаюсь цим", callback_data=f"ack:{alert_id}")]]
    if allow_swap:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="Створити / активувати Swap", callback_data=f"swap:{alert_id}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
