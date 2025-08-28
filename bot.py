from background_worker import keep_alive
keep_alive()
from supabase import create_client, Client
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ChatMemberUpdated
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import asyncio
import logging
import os
import json
from dotenv import load_dotenv
load_dotenv()

# Supabase
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# Telegram
TELEGRAM_KEY = os.environ.get("TELEGRAM_KEY")
# Example formats for GROUP_CHAT_IDS env:
# JSON: {"2": -1001234567890, "2к1": -1002345678901}
# Python dict: {'2': -1001234567890, '2к1': -1002345678901}
GROUP_CHAT_IDS_RAW = os.environ.get("GROUP_CHAT_IDS", "{}")
try:
    parsed_mapping = json.loads(GROUP_CHAT_IDS_RAW)
except Exception:
    try:
        import ast
        parsed_mapping = ast.literal_eval(GROUP_CHAT_IDS_RAW)
    except Exception:
        logging.error("Failed to parse GROUP_CHAT_IDS env variable. Provide JSON or Python dict mapping of building->chat_id")
        parsed_mapping = {}

try:
    GROUP_CHAT_IDS: dict[str, int] = {str(k): int(v) for k, v in dict(parsed_mapping).items()}
except Exception:
    logging.error("GROUP_CHAT_IDS contains non-numeric chat ids; please use integers (e.g., -1001234567890)")
    GROUP_CHAT_IDS = {}

# Admin chats (per building), same format as GROUP_CHAT_IDS
ADMIN_CHAT_IDS_RAW = os.environ.get("ADMIN_CHAT_IDS", "{}")
try:
    admin_parsed_mapping = json.loads(ADMIN_CHAT_IDS_RAW)
except Exception:
    try:
        import ast
        admin_parsed_mapping = ast.literal_eval(ADMIN_CHAT_IDS_RAW)
    except Exception:
        logging.error("Failed to parse ADMIN_CHAT_IDS env variable. Provide JSON or Python dict mapping of building->chat_id")
        admin_parsed_mapping = {}

try:
    ADMIN_CHAT_IDS: dict[str, int] = {str(k): int(v) for k, v in dict(admin_parsed_mapping).items()}
except Exception:
    logging.error("ADMIN_CHAT_IDS contains non-numeric chat ids; please use integers (e.g., -1001234567890)")
    ADMIN_CHAT_IDS = {}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_KEY)
dp = Dispatcher()


def resolve_building_chat_id(building: str) -> int | None:
    return GROUP_CHAT_IDS.get(building)


def resolve_chat_building(chat_id: int) -> str | None:
    for building_name, configured_chat_id in GROUP_CHAT_IDS.items():
        if configured_chat_id == chat_id:
            return building_name
    return None


def resolve_building_admin_chat_id(building: str) -> int | None:
    return ADMIN_CHAT_IDS.get(building)


async def create_one_time_invite_link(building: str) -> str | None:
    try:
        chat_id = resolve_building_chat_id(building)
        if chat_id is None:
            return None
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1
        )
        return invite.invite_link
    except Exception as err:
        logging.error(f"Error creating invite link for building {building}: {err}")
        return None


# Handlers
# /start: show entry inline buttons (only in private chats)
@dp.message(CommandStart(), F.chat.type == "private")
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
            [KeyboardButton(text="❌ Не согласен"), KeyboardButton(text="✅ Согласен")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(JoinChat.consent_share_flat)
    await callback.message.answer(
        f"Пожалуйста, подтвердите согласие на обработку номера вашей квартиры и данных Telegram-аккаунта. Это необходимо для проверки подлинности соседства и добавления вас в чат.\n\nПолитика конфиденциальности: https://clck.ru/3NqANx",
        reply_markup=keyboard,
        disable_web_page_preview=True
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

    # If building is not supported, keep selection and inform the user
    if resolve_building_chat_id(selected) is None:
        keyboard = InlineKeyboardBuilder()
        building_names = ["2", "2к1", "2к4", "2к5"]
        for building_name in building_names:
            keyboard.button(
                text=building_name,
                callback_data=f"building_{building_name}"
            )
        keyboard.adjust(4)
        await state.set_state(JoinChat.selecting_building)
        await callback.message.edit_text(
            f"К сожалению, дом {selected} пока не поддерживается. Выберите другой дом:",
            reply_markup=keyboard.as_markup()
        )
        return

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

        processing_message = await message.answer("⏳ Обрабатываю данные, пожалуйста подождите…")

        async def finish(text: str):
            try:
                await processing_message.edit_text(text)
            except Exception:
                await message.answer(text)
            await state.clear()

        # Exact-duplicate check (allow multiple flats, but not the same flat twice)
        existing_flat = supabase.table("users").select("id").eq("telegram_id", telegram_id).eq("building", building).eq("flat_number", flat_number).execute()

        # Insert record if it's not an exact duplicate
        if not existing_flat.data:
            user_data = {
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "building": building,
                "flat_number": flat_number,
                "joined_at": "now()"
            }
            try:
                supabase.table("users").insert(user_data).execute()
            except Exception as insert_err:
                logging.error(f"Insert failed (continuing as duplicate-safe): {insert_err}")
                # If a UNIQUE constraint exists server-side, treat as duplicate and continue

        # Always produce an invite link regardless of DB path
        invite_link = await create_one_time_invite_link(building)

        # Build base response once
        def build_response(prefix: str) -> str:
            text = prefix
            if resolve_building_chat_id(building) is None:
                text += "\n\nК сожалению, чат для этого дома пока не подключен. Свяжитесь с администратором @xmlChay (Илья)."
                return text
            if invite_link:
                text += f"\n\n🔗 Ссылка для вступления в чат: {invite_link}"
            else:
                text += "\n\nНе удалось создать ссылку приглашения. Свяжитесь с администратором @xmlChay (Илья)"
            return text

        response = build_response(f"Готово! Дом {building}, квартира {flat_number}.")

        await finish(response)

    except Exception as e:
        logging.error(f"Error storing user data: {e}")
        await message.answer(
            "Произошла ошибка при сохранении данных. Пожалуйста, обратитесь к разработчику @xmlChay (Илья)"
        )


# Message: invalid flat number → re-prompt
@dp.message(JoinChat.awaiting_flat_number)
async def on_flat_number_invalid(message: Message):
    await message.answer("Пожалуйста, укажите корректный номер квартиры")


# Chat member update handler - detect when users leave the group
@dp.chat_member()
async def on_chat_member_update(update: ChatMemberUpdated):
    # Only process updates for our target groups
    if update.chat.id not in GROUP_CHAT_IDS.values():
        return
    
    # Check if user left the chat
    if update.old_chat_member.status in ["member", "administrator", "creator"] and update.new_chat_member.status == "left":
        building = resolve_chat_building(update.chat.id)
        if building is None:
            return

        # The affected user is the one in new_chat_member
        user_id = update.new_chat_member.user.id
        username = update.new_chat_member.user.username or "Unknown"
        
        try:
            # Get all flats for this user in this building only
            user_flats = (
                supabase
                .table("users")
                .select("*")
                .eq("telegram_id", user_id)
                .eq("building", building)
                .execute()
            )
            
            if user_flats.data:
                # Delete only flats for this user in this building
                supabase.table("users").delete().eq("telegram_id", user_id).eq("building", building).execute()
                
                logging.info(
                    f"User {username} (ID: {user_id}) left chat {update.chat.id} ({building}). "
                    f"Removed {len(user_flats.data)} flat(s) for this building from database."
                )
                
                # Notify user in private message about data deletion
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=f"👋 {username or 'Пользователь'}, вы покинули чат соседей по дому {building}.\n\n"
                             f"Ваши данные по этому дому ({len(user_flats.data)} квартир(а)) были удалены из базы данных "
                             f"в соответствии с политикой конфиденциальности.\n\n"
                             f"Если вы захотите вернуться в чат, просто начните заново с команды /start"
                    )
                except Exception as notify_error:
                    logging.error(f"Error notifying user about data deletion: {notify_error}")
                    # User might have blocked the bot or deleted their account
                
        except Exception as e:
            logging.error(f"Error removing user data when leaving group: {e}")

    # Check if user joined the chat
    if update.old_chat_member.status in ["left", "kicked"] and update.new_chat_member.status in ["member", "administrator", "creator"]:
        building = resolve_chat_building(update.chat.id)
        if building is None:
            return

        user_id = update.new_chat_member.user.id
        username = update.new_chat_member.user.username or "Unknown"
        first_name = update.new_chat_member.user.first_name or "Unknown"
        last_name = update.new_chat_member.user.last_name or ""

        try:
            # Fetch user data for this building, if any
            user_flats = (
                supabase
                .table("users")
                .select("*")
                .eq("telegram_id", user_id)
                .eq("building", building)
                .execute()
            )

            admin_chat_id = resolve_building_admin_chat_id(building)
            if not admin_chat_id:
                return

            if user_flats.data:
                flats_lines = []
                for rec in user_flats.data:
                    flat_no = rec.get("flat_number", "—")
                    flats_lines.append(f"Квартира: {flat_no}")
                flats_text = "\n".join(flats_lines)
                msg = (
                    "✅ Пользователь присоединился к чату\n\n"
                    f"Дом: {building}\n"
                    f"Пользователь: @{username if username != 'Unknown' else '—'} (ID: {user_id})\n"
                    f"Имя: {first_name} {last_name}".strip() + "\n\n"
                    f"Данные: \n{flats_text}"
                )
            else:
                msg = (
                    "ℹ️ Пользователь присоединился к чату, но данные не найдены в базе\n\n"
                    f"Дом: {building}\n"
                    f"Пользователь: @{username if username != 'Unknown' else '—'} (ID: {user_id})\n"
                    f"Имя: {first_name} {last_name}".strip()
                )

            await bot.send_message(chat_id=admin_chat_id, text=msg)
        except Exception as e:
            logging.error(f"Error notifying admins about user join: {e}")


# /revoke: user-initiated data deletion (private only)
@dp.message(Command("revoke"), F.chat.type == "private")
async def revoke_request(message: Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="❌ Отмена", callback_data="revoke_cancel")
    keyboard.button(text="✅ Подтвердить", callback_data="revoke_confirm")
    keyboard.adjust(1)
    warn_text = (
        "Внимание: удаление ваших данных приведет к вашему удалению из чатов соседей.\n\n"
        "Вы действительно хотите отозвать согласие на обработку данных и удалить свои данные?"
    )
    await message.answer(warn_text, reply_markup=keyboard.as_markup())


@dp.callback_query(F.data == "revoke_cancel")
async def revoke_cancel(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("Операция отменена.")


@dp.callback_query(F.data == "revoke_confirm")
async def revoke_confirm(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    # Delete user data from Supabase
    try:
        user_flats = supabase.table("users").select("*").eq("telegram_id", user_id).execute()
        deleted_count = 0
        if user_flats.data:
            supabase.table("users").delete().eq("telegram_id", user_id).execute()
            deleted_count = len(user_flats.data)
    except Exception as e:
        logging.error(f"Revoke: error deleting user data: {e}")
        await callback.message.edit_text(
            "Произошла ошибка при удалении данных. Попробуйте позже или обратитесь к разработчику @xmlChay (Илья)"
        )
        return

    # Try to remove the user from all configured group chats
    removed_from = 0
    for chat_id in GROUP_CHAT_IDS.values():
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            removed_from += 1
        except Exception as err:
            # Might fail if bot is not admin or user not in the chat; ignore per chat
            logging.info(f"Revoke: could not remove user {user_id} from chat {chat_id}: {err}")
            continue

    await callback.message.edit_text(
        (
            "Ваши данные удалены. "
            + (f"Удалено записей: {deleted_count}. " if deleted_count else "")
            + (
                f"Вы удалены из {removed_from} чата(ов)."
                if removed_from
                else "Не удалось удаленно исключить из чатов или вы не были участником."
            )
        )
    )

async def main():
    await dp.start_polling(bot)

keep_alive()
if __name__ == '__main__':
    asyncio.run(main())