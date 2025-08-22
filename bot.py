from supabase import create_client, Client
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import asyncio
import logging
import os
from dotenv import load_dotenv
load_dotenv()

# Supabase
# url: str = os.environ.get("SUPABASE_URL")
# key: str = os.environ.get("SUPABASE_KEY")
# supabase: Client = create_client(url, key)

# Telegram
TELEGRAM_KEY = os.environ.get("TELEGRAM_KEY")
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_KEY)
dp = Dispatcher()

# Handlers
# /start: show entry inline buttons
@dp.message(CommandStart())
async def handle_start_command(message: Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="ℹ️ Узнать о доме", callback_data="start_get_info")
    keyboard.button(text="💬 Вступить в чат", callback_data="start_join_chat")
    keyboard.adjust(2)
    await message.answer(
        "👋 Привет! Я бот-помощник для соседей. Выберите действие:",
        reply_markup=keyboard.as_markup()
    )


# FSM: states for the join chat flow
class JoinChat(StatesGroup):
    selecting_building = State()
    consent_share_flat = State()
    awaiting_flat_number = State()


# Callback: ℹ️ Узнать о доме
@dp.callback_query(F.data == "start_get_info")
async def on_get_info(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer("⚒️ Раздел в разработке...")


# Callback: 💬 Вступить в чат
@dp.callback_query(F.data == "start_join_chat")
async def on_join_chat(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    keyboard = InlineKeyboardBuilder()
    building_names = ["2", "2к1", "2к4", "2к5"]
    for building_name in building_names:
        keyboard.button(
            text=building_name,
            callback_data=f"building_{building_name}"
        )
    keyboard.adjust(4)
    await state.set_state(JoinChat.selecting_building)
    await callback.message.answer(
        "Выберите дом, который вас интересует:",
        reply_markup=keyboard.as_markup()
    )


# Callback: building selected → ask consent to share flat number
@dp.callback_query(JoinChat.selecting_building, F.data.startswith("building_"))
async def on_building_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    selected = callback.data.split("_")[-1]
    await state.update_data(building=selected)

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="❌ Нет", callback_data="consent_no")
    keyboard.button(text="✅ Да", callback_data="consent_yes")
    keyboard.adjust(2)

    await state.set_state(JoinChat.consent_share_flat)
    await callback.message.answer(
        "Вы согласны предоставить номер своей квартиры и привязать ее к этому Telegram-аккаунту? Это позволит соседям быстро связаться с вами при возникновении аварий и необходимо для вступления в чат",
        reply_markup=keyboard.as_markup()
    )


# Callback: ✅ Yes
@dp.callback_query(JoinChat.consent_share_flat, F.data == "consent_yes")
async def on_consent_yes(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(JoinChat.awaiting_flat_number)
    await callback.message.answer("Пожалуйста, сообщите номер своей квартиры")


# Callback: ❌No
@dp.callback_query(JoinChat.consent_share_flat, F.data == "consent_no")
async def on_consent_no(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer("Понимаю. К сожалению, без номера квартиры я не смогу добавить вас в чат")


# Message: valid flat number received → confirm and clear state
@dp.message(JoinChat.awaiting_flat_number, F.text.regexp(r"^\d{1,5}$"))
async def on_flat_number(message: Message, state: FSMContext):
    data = await state.get_data()
    building = data.get("building")
    flat_number = message.text
    await state.clear()
    await message.answer(
        "Отлично! Вы успешно добавлены в чат соседей"
    )


# Message: invalid flat number → re-prompt
@dp.message(JoinChat.awaiting_flat_number)
async def on_flat_number_invalid(message: Message):
    await message.answer("Пожалуйста, укажите корректный номер квартиры")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())