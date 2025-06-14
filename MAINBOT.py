import asyncio
import json
import os
import re
import httpx
from uuid import uuid4
from collections import defaultdict
from telethon import TelegramClient, events
from openai import OpenAI
from aiogram import types, Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto
)
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

proxy_url = "http://HMUq9KXX:CQFrLgNe@166.88.218.87:62738"
os.environ["HTTPS_PROXY"] = proxy_url
os.environ["HTTP_PROXY"] = proxy_url

SESSION_NAME = os.getenv("SESSION_NAME")
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
CHAT_ID = int(os.getenv("CHAT_ID"))
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PUBLISH_QUEUE_FILE = "publish_queue.json"

transport = httpx.HTTPTransport(proxy=proxy_url)
http_client = httpx.Client(
    transport=transport,
    headers={
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "HTTP-Referer": "https://chat.openai.com"
    }
)
openai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    http_client=http_client
)



SOURCE_CHANNELS = [
    'rian_ru', 'exploitex', 'smi_rf_moskva', 'media1337', 'novosti_efir',
    'mash', 'readovkanews', 'ScreenShotTrue', 'ostorozhno_novosti', 'Ria_novosti_rossiya'
]

GPT_PROMPT = (
    "Ты — новостной редактор. Твоя задача — переформулировать новость без искажения фактов и с сохранением нейтрального, объективного тона. "
    "Избегай иронии, юмора, преувеличений, оценочных суждений. Просто перефразируй новость так, чтобы она выглядела уникально (антиплагиат), сохраняя точную суть событий.\n\n"
    "Удаляй в конце любые призывы к подписке на Telegram-каналы, а также названия каналов или подписи к ним:\n"
    "— '🌒 1337', '🔥 ExploiteX', '📡 SMIRF Москва', '🗞 media1337', '📺 Mash', '🛑 Осторожно, новости', '🛰 RIA Новости',\n"
    "— '📷 ScreenshotTrue', 'Readovka', 'Бобмэнст', 'Новостной эфир' и т.д.\n"
    "Не добавляй ничего от себя, не переиначивай интонацию. Только точная переформулировка."
)


client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
group_buffers = defaultdict(list)
group_tasks = {}
group_buffer_lock = asyncio.Lock()
pending_posts = {}
editing_users = {}

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher(storage=MemoryStorage())


def clean_gpt_output(text: str) -> str:
    return re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ\n\r.,!?:;()\[\]«»\"' + "'" + r' \-]+$', '', text).strip()


async def ask_gpt(text: str) -> str:
    if len(text.strip()) < 10:
        return text
    try:
        response = openai_client.chat.completions.create(
            model='gpt-3.5-turbo',
            messages=[
                {"role": "system", "content": "Ты редактор новостей."},
                {"role": "user", "content": f"{GPT_PROMPT}\n\n{text}"}
            ]
        )
        result = clean_gpt_output(response.choices[0].message.content.strip())
        return result or "[Текст от GPT пуст — добавьте вручную]"
    except Exception as e:
        print(f"❌ GPT ошибка: {e}")
        return "[GPT не сработал — отредактируйте текст]"


def get_publish_keyboard(post_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"approve_post:{post_id}"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"decline_post:{post_id}")
            ],
            [
                InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_post:{post_id}")
            ]
        ]
    )


async def send_publish_prompt(chat_id: int, media_paths: list, video_paths: list, caption: str):
    post_id = str(uuid4())
    pending_posts[post_id] = {
        "media": media_paths,
        "videos": video_paths,
        "caption": caption
    }
    success = False

    if media_paths:
        try:
            group = [InputMediaPhoto(media=FSInputFile(p)) for p in media_paths[:10]]
            await bot.send_media_group(chat_id, group)
            success = True
        except Exception as e:
            print(f"❌ Ошибка предпросмотра фото: {e}")

    for path in video_paths:
        try:
            await bot.send_video(chat_id, video=FSInputFile(path))
            success = True
        except Exception as e:
            print(f"❌ Ошибка предпросмотра видео: {e}")

    if caption:
        try:
            await bot.send_message(chat_id, f"*Текст поста:*\n\n{caption}")
            success = True
        except Exception as e:
            print(f"❌ Ошибка предпросмотра текста: {e}")

    if success:
        await bot.send_message(chat_id, "Хотите опубликовать пост?", reply_markup=get_publish_keyboard(post_id))


@dp.callback_query(F.data.startswith("approve_post:"))
async def approve_post(callback: CallbackQuery):
    post_id = callback.data.split(":")[1]
    post = pending_posts.pop(post_id, None)
    if not post:
        return await callback.answer("⛔ Пост не найден", show_alert=True)
    try:
        with open(PUBLISH_QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(post, f, ensure_ascii=True, indent=2)
        await callback.message.edit_text("📨 Пост отправлен юзерботу на публикацию")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")


@dp.callback_query(F.data.startswith("decline_post:"))
async def decline_post(callback: CallbackQuery):
    post_id = callback.data.split(":")[1]
    _ = pending_posts.pop(post_id, None)
    await callback.message.edit_text("❌ Пост отклонён.")


@dp.callback_query(F.data.startswith("edit_post:"))
async def edit_post(callback: CallbackQuery):
    post_id = callback.data.split(":")[1]
    post = pending_posts.get(post_id)
    if not post:
        await callback.answer("⛔ Пост не найден", show_alert=True)
        return
    editing_users[callback.from_user.id] = {"post_id": post_id, "stage": "choose"}
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Текст", callback_data="edit_text")],
            [InlineKeyboardButton(text="🖼 Медиа", callback_data="edit_media")]
        ]
    )
    await callback.message.answer("Что хотите отредактировать?", reply_markup=keyboard)


@dp.callback_query(F.data.in_(["edit_text", "edit_media"]))
async def choose_edit_type(callback: CallbackQuery):
    user_id = callback.from_user.id
    edit_type = callback.data.split("_")[1]
    if user_id not in editing_users:
        return await callback.answer("⛔ Сессия редактирования не найдена", show_alert=True)
    editing_users[user_id]["stage"] = edit_type
    prompt = "✏️ Пришлите новый текст поста:" if edit_type == "text" else "📎 Пришлите новые медиа (фото или видео)"
    await callback.message.answer(prompt)


@dp.message()
async def handle_edit_input(message: types.Message):
    user_id = message.from_user.id
    if user_id not in editing_users:
        return
    session = editing_users[user_id]
    post_id = session["post_id"]
    stage = session["stage"]
    if post_id not in pending_posts:
        return await message.answer("⛔ Пост не найден.")
    if stage == "text":
        pending_posts[post_id]["caption"] = message.text.strip()
    elif stage == "media":
        media_paths, video_paths = [], []
        if message.photo:
            file_path = f"media_{message.message_id}.jpg"
            await message.bot.download(message.photo[-1], destination=file_path)
            media_paths.append(file_path)
        if message.video:
            file = await message.video.download()
            video_paths.append(file.name)
        if media_paths or video_paths:
            pending_posts[post_id]["media"] = media_paths
            pending_posts[post_id]["videos"] = video_paths
        else:
            return await message.answer("⛔ Не удалось получить медиа.")
    editing_users[user_id]["stage"] = "confirm"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="confirm_back")]
        ]
    )
    await message.answer("Это всё? Отправляем?", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("confirm_"))
async def handle_confirm(callback: CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split("_")[1]
    if user_id not in editing_users:
        return await callback.answer("⛔ Нет активной сессии", show_alert=True)
    post_id = editing_users[user_id]["post_id"]
    if post_id not in pending_posts:
        return await callback.answer("⛔ Пост не найден", show_alert=True)
    if action == "yes":
        del editing_users[user_id]
        await callback.message.answer("✅ Пост обновлён.")
        await send_publish_prompt(callback.message.chat.id,
                                  pending_posts[post_id]["media"],
                                  pending_posts[post_id]["videos"],
                                  pending_posts[post_id]["caption"])
    elif action == "no":
        editing_users[user_id]["stage"] = "choose"
        await callback.message.answer("Что хотите отредактировать ещё раз?",
                                      reply_markup=InlineKeyboardMarkup(
                                          inline_keyboard=[
                                              [InlineKeyboardButton(text="📝 Текст", callback_data="edit_text")],
                                              [InlineKeyboardButton(text="🖼 Медиа", callback_data="edit_media")]
                                          ]
                                      ))
    elif action == "back":
        del editing_users[user_id]
        await callback.message.answer("🔙 Возврат к меню поста.", reply_markup=get_publish_keyboard(post_id))


@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handle(event):
    grouped_id = event.message.grouped_id
    if grouped_id:
        async with group_buffer_lock:
            group_buffers[grouped_id].append(event)
            if grouped_id in group_tasks:
                return
            task = asyncio.create_task(schedule_handle_group(grouped_id))
            group_tasks[grouped_id] = task
    else:
        await process_messages([event])


async def schedule_handle_group(grouped_id):
    await asyncio.sleep(2.5)
    async with group_buffer_lock:
        messages = group_buffers.pop(grouped_id, [])
        group_tasks.pop(grouped_id, None)
    await process_messages(messages)


async def process_messages(messages):
    try:
        if not messages:
            return
        text = ""
        for msg in messages:
            if hasattr(msg, "message"):
                possible_text = getattr(msg.message, "message", None)
                if isinstance(possible_text, str) and possible_text.strip():
                    text = possible_text
                    break
        caption = await ask_gpt(text)
        media_paths, video_paths = [], []
        for msg in messages:
            if not msg.file:
                continue
            file_path = await msg.download_media()
            if msg.file.mime_type and "video" in msg.file.mime_type:
                video_paths.append(file_path)
            else:
                media_paths.append(file_path)
        await send_publish_prompt(CHAT_ID, media_paths, video_paths, caption)
    except Exception as e:
        print(f"❌ Ошибка обработки: {e}")


async def check_publish_queue():
    while True:
        if os.path.exists(PUBLISH_QUEUE_FILE):
            try:
                with open(PUBLISH_QUEUE_FILE, "r", encoding="utf-8") as f:
                    post = json.load(f)
                os.remove(PUBLISH_QUEUE_FILE)
                media = post.get("media", [])
                videos = post.get("videos", [])
                caption = post.get("caption", "").strip()
                if caption:
                    caption += "\n\nПодписаться на [Гром](https://t.me/newgrom)"
                files = media + videos
                if files:
                    await client.send_file(TARGET_CHANNEL, file=files, caption=caption)
                else:
                    await client.send_message(TARGET_CHANNEL, caption)
                print("✅ Пост опубликован")
            except Exception as e:
                print(f"❌ Ошибка публикации: {e}")
        await asyncio.sleep(2)


async def start_aiogram():
    await dp.start_polling(bot)


async def main():
    asyncio.create_task(start_aiogram())
    asyncio.create_task(check_publish_queue())
    await client.start()
    print("✅ Юзербот запущен")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
