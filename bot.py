from supabase import create_client, Client
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
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
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

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
    consent_share_flat = State()
    selecting_building = State()
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
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Согласен"), KeyboardButton(text="❌ Не согласен")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(JoinChat.consent_share_flat)
    await callback.message.answer(
        "Пожалуйста, подтвердите согласие на обработку номера вашей квартиры и данных Telegram-аккаунта. Это необходимо для проверки подлинности соседства и добавления вас в чат",
        reply_markup=keyboard
    )


# Message: consent response → ask for building or decline
@dp.message(JoinChat.consent_share_flat, F.text.in_(["✅ Согласен"]))
async def on_consent_yes(message: Message, state: FSMContext):
    keyboard = InlineKeyboardBuilder()
    building_names = ["2", "2к1", "2к4", "2к5"]
    for building_name in building_names:
        keyboard.button(
            text=building_name,
            callback_data=f"building_{building_name}"
        )
    keyboard.adjust(4)
    await state.set_state(JoinChat.selecting_building)
    await message.answer(
        "Выберите дом, который вас интересует:",
        reply_markup=keyboard.as_markup()
    )


# Message: consent declined
@dp.message(JoinChat.consent_share_flat, F.text.in_(["❌ Не согласен"]))
async def on_consent_no(message: Message, state: FSMContext):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🔄 Передумал", callback_data="start_join_chat")
    keyboard.adjust(1)
    await message.answer(
        "Понимаю. К сожалению, без согласия на обработку данных я не смогу добавить вас в чат",
        reply_markup=keyboard.as_markup()
    )


# Message: other text in consent state
@dp.message(JoinChat.consent_share_flat)
async def on_consent_invalid(message: Message):
    await message.answer("Пожалуйста, используйте кнопки для ответа: '✅ Согласен' или '❌ Не согласен'")


# Callback: building selected → ask for flat number
@dp.callback_query(JoinChat.selecting_building, F.data.startswith("building_"))
async def on_building_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    selected = callback.data.split("_")[-1]
    await state.update_data(building=selected)
    await state.set_state(JoinChat.awaiting_flat_number)
    await callback.message.edit_text(
        "Пожалуйста, сообщите номер своей квартиры",
        reply_markup=None
    )


# Message: valid flat number received → confirm and clear state
@dp.message(JoinChat.awaiting_flat_number, F.text.regexp(r"^\d{1,5}$"))
async def on_flat_number(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        building = data.get("building")
        flat_number = message.text
        telegram_id = message.from_user.id
        username = message.from_user.username or "Unknown"
        first_name = message.from_user.first_name or "Unknown"
        last_name = message.from_user.last_name or ""
        
        # Check if this specific flat for this user already exists
        existing_flat = supabase.table("users").select("*").eq("telegram_id", telegram_id).eq("building", building).eq("flat_number", flat_number).execute()
        
        if existing_flat.data:
            # This specific flat already exists for this user
            await state.clear()
            await message.answer(
                f"Вы уже зарегистрированы в чате соседей дома {building}, квартира {flat_number}. Дублирование не требуется."
            )
        else:
            # Check if user exists but with different flat/building
            existing_user = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
            
            if existing_user.data:
                # User exists, add new flat record
                user_data = {
                    "telegram_id": telegram_id,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "building": building,
                    "flat_number": flat_number,
                    "joined_at": "now()"
                }
                
                result = supabase.table("users").insert(user_data).execute()
                
                await state.clear()
                await message.answer(
                    f"Отлично! Добавлена новая квартира: дом {building}, квартира {flat_number}"
                )
            else:
                # New user, insert first record
                user_data = {
                    "telegram_id": telegram_id,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "building": building,
                    "flat_number": flat_number,
                    "joined_at": "now()"
                }
                
                result = supabase.table("users").insert(user_data).execute()
                
                await state.clear()
                await message.answer(
                    f"Отлично! Вы успешно добавлены в чат соседей дома {building}, квартира {flat_number}. Данные сохранены в базе."
                )
        
    except Exception as e:
        logging.error(f"Error storing user data: {e}")
        await message.answer(
            "Произошла ошибка при сохранении данных. Пожалуйста, обратитесь к разработчику @xmlChay (Илья)"
        )


# Message: invalid flat number → re-prompt
@dp.message(JoinChat.awaiting_flat_number)
async def on_flat_number_invalid(message: Message):
    await message.answer("Пожалуйста, укажите корректный номер квартиры")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())