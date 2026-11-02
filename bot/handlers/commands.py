from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

router = Router()


def is_group(message):
    return message.chat.type in ("group", "supergroup")


@router.message(CommandStart())
async def start(message: Message, backend):
    if not is_group(message):
        await message.answer(
            "Додайте бота до групи клієнта. Авторизація доступна головному адміністратору."
        )
        return
    reports = await backend.reports(message.chat.id)
    await message.answer(
        "Група вже підключена. /status — стан серверів.\n"
        "Для додаткового сервера адміністратор може надіслати код командою /bind КОД."
        if reports
        else "Сервер ще не авторизований. Головний адміністратор має "
        "надіслати 15-значний код або /bind КОД."
    )


@router.message(Command("help"))
async def help_message(message: Message):
    await message.answer(
        "/start — підключення\n/status — останній стан\n/report — позачергова перевірка "
        "та звіт\n/bind КОД — підключення сервера (лише адміністратор)\n"
        "/help — команди\nКритичні кнопки доступні лише головному адміністратору."
    )


@router.message(Command("status", "report"))
async def status(message: Message, backend):
    if not is_group(message):
        return
    refresh = message.text.split()[0].split("@")[0] == "/report"
    try:
        reports = await backend.reports(message.chat.id, refresh)
    except ValueError as error:
        await message.answer(str(error))
        return
    if not reports:
        await message.answer("Спочатку авторизуйте сервер: /start.")
        return
    for text in reports:
        await message.answer(text)
    if refresh:
        await message.answer(
            "Позачергову перевірку запитано; свіжий звіт надійде після її завершення."
        )


@router.message(Command("bind"))
@router.message(F.text.regexp(r"^[0-9]{15}$"))
async def bind(message: Message, backend):
    if not is_group(message) or not message.from_user:
        return
    code = message.text.split(maxsplit=1)[-1].strip()
    try:
        name = await backend.bind(code, message.chat.id, message.from_user.id)
        await message.answer(f"✅ Сервер {escape(name)} успішно підключено до цієї групи.")
        try:
            await message.delete()
        except Exception:
            pass
    except (ValueError, PermissionError) as error:
        await message.answer(escape(str(error)))


@router.callback_query(F.data.regexp(r"^(ack|swap):[0-9]+$"))
async def callback(query: CallbackQuery, backend):
    if not query.message or not query.from_user:
        return
    operation, identifier = query.data.split(":")
    try:
        await backend.callback(
            operation, int(identifier), query.message.chat.id, query.from_user.id
        )
    except (PermissionError, ValueError) as error:
        await query.answer(str(error), show_alert=True)
        return
    await query.answer(
        "Аварію взято в роботу." if operation == "ack" else "Операцію поставлено в чергу."
    )
    await query.message.answer(
        "🛠 Адміністратор займається аварією."
        if operation == "ack"
        else "⏳ Запитано SWAP +1 GB. Результат надійде окремим повідомленням."
    )
