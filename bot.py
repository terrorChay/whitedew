from background_worker import keep_alive
keep_alive()
from supabase import create_client, Client
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ChatMemberUpdated, ChatJoinRequest
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

# Shared chat for the whole complex, offered on top of the building chat
PUBLIC_CHAT_ID_RAW = os.environ.get("PUBLIC_CHAT_ID", "").strip()
try:
    PUBLIC_CHAT_ID: int | None = int(PUBLIC_CHAT_ID_RAW) if PUBLIC_CHAT_ID_RAW else None
except Exception:
    logging.error("PUBLIC_CHAT_ID must be an integer chat id (e.g., -1001234567890)")
    PUBLIC_CHAT_ID = None

# Every building of the complex, including those without their own chat yet:
# their residents still register and get access to the shared chat
# Example format for BUILDINGS env: 2,2к1,2к4,2к5
BUILDINGS_RAW = os.environ.get("BUILDINGS", "")
BUILDINGS: list[str] = list(dict.fromkeys(
    [part.strip() for part in BUILDINGS_RAW.split(",") if part.strip()] + list(GROUP_CHAT_IDS)
))

# Users allowed to run admin commands in any building chat, regardless of their status there
# Example format for OWNER_IDS env: 230720971,987654321
OWNER_IDS_RAW = os.environ.get("OWNER_IDS", "")
try:
    OWNER_IDS: set[int] = {int(part.strip()) for part in OWNER_IDS_RAW.split(",") if part.strip()}
except Exception:
    logging.error("OWNER_IDS must be a comma-separated list of Telegram user ids (e.g., 230720971)")
    OWNER_IDS = set()

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


def all_connected_chat_ids() -> list[int]:
    chat_ids = list(GROUP_CHAT_IDS.values())
    if PUBLIC_CHAT_ID is not None:
        chat_ids.append(PUBLIC_CHAT_ID)
    return list(dict.fromkeys(chat_ids))


def is_connected_chat(chat_id: int) -> bool:
    return chat_id in all_connected_chat_ids()


def resolve_chat_title(chat_id: int) -> str:
    building = resolve_chat_building(chat_id)
    if building is not None:
        return f"Чат дома {building}"
    if PUBLIC_CHAT_ID is not None and chat_id == PUBLIC_CHAT_ID:
        return "Общий чат ЖК"
    return "Чат"


def build_building_keyboard() -> InlineKeyboardBuilder:
    keyboard = InlineKeyboardBuilder()
    for building_name in BUILDINGS:
        keyboard.button(
            text=building_name,
            callback_data=f"building_{building_name}"
        )
    keyboard.adjust(4)
    return keyboard


async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return getattr(member, "status", None) in ["administrator", "creator"]
    except Exception as err:
        logging.info(f"Admin check failed for user {user_id} in chat {chat_id}: {err}")
        return False


async def can_use_admin_commands(message: Message) -> bool:
    # Anonymous admins post on behalf of the group itself
    if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
        return True
    if message.from_user is None:
        return False
    if message.from_user.id in OWNER_IDS:
        return True
    return await is_chat_admin(message.chat.id, message.from_user.id)


def format_user_name(user: types.User) -> str:
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Сосед"
    return f"{full_name} (@{user.username})" if user.username else full_name


async def answer_admin_privately(message: Message, text: str) -> None:
    # Anonymous admins act on behalf of the group and have no personal chat with the bot
    if message.sender_chat is None and message.from_user is not None:
        try:
            await bot.send_message(chat_id=message.from_user.id, text=text)
            return
        except Exception as err:
            logging.info(f"Could not answer admin {message.from_user.id} privately: {err}")
            text += "\n\nЧтобы получать ответы бота в личных сообщениях, откройте диалог с ботом и отправьте /start"
    await message.answer(text)


async def is_user_in_chat(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        status = getattr(member, "status", None)
        return status in ["member", "administrator", "creator"]
    except Exception as err:
        logging.info(f"Membership check failed for user {user_id} in chat {chat_id}: {err}")
        return False


async def create_one_time_invite_link(chat_id: int) -> str | None:
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1
        )
        return invite.invite_link
    except Exception as err:
        logging.error(f"Error creating invite link for chat {chat_id}: {err}")
        return None


# Handlers
# /start: show entry inline buttons (only in private chats)
@dp.message(CommandStart(), F.chat.type == "private")
async def handle_start_command(message: Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="ℹ️ Узнать о доме", callback_data="start_get_info")
    keyboard.button(text="💬 Вступить в чат", callback_data="start_join_chat")
    keyboard.button(text="🚨 Сообщить о проблеме в доме", callback_data="start_report_emergency")
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


# Callback: 🚨 Сообщить об аварии
@dp.callback_query(F.data == "start_report_emergency")
async def on_report_emergency(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="Госуслуги.Дом", url="https://www.gosuslugi.ru/landing/mp_dom")
    kb.button(text="Добродел", url="https://dobrodel.mosreg.ru")
    kb.button(text="Единая диспетчерская служба", url="https://eds.mosreg.ru/")
    kb.adjust(2)
    await callback.message.answer(
        "Сообщить об аварии, некачественном содержании дома и двора можно в сервисах:",
        reply_markup=kb.as_markup(),
        disable_web_page_preview=True
    )


def has_user_record(telegram_id: int, building: str | None = None) -> bool:
    try:
        query = supabase.table("users").select("id").eq("telegram_id", telegram_id)
        if building is not None:
            query = query.eq("building", building)
        return bool(query.limit(1).execute().data)
    except Exception as err:
        # Treat a lookup failure as "no record": ask again instead of trusting a broken check
        logging.error(f"Record lookup failed for user {telegram_id}: {err}")
        return False


# Consent is remembered through the records the user already has in the database
def has_given_consent(telegram_id: int) -> bool:
    return has_user_record(telegram_id)


async def prompt_building_selection(message: Message, state: FSMContext, intro: str | None = None) -> None:
    if not BUILDINGS:
        await state.clear()
        await message.answer(
            "К сожалению, сейчас не подключен ни один чат. Свяжитесь с администратором @xmlChay (Илья)."
        )
        return

    text = "Выберите дом, который вас интересует:"
    if intro:
        text = f"{intro}\n\n{text}"

    await state.set_state(JoinChat.selecting_building)
    await message.answer(text, reply_markup=build_building_keyboard().as_markup())


# Callback: 💬 Вступить в чат
@dp.callback_query(F.data == "start_join_chat")
async def on_join_chat(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    if has_given_consent(callback.from_user.id):
        await prompt_building_selection(
            callback.message,
            state,
            intro="Вы уже давали согласие на обработку данных, поэтому спрашивать повторно не буду. Отозвать его можно командой /revoke."
        )
        return

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
    await prompt_building_selection(message, state)


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
    selected = callback.data.split("_", 1)[1]

    # Config may have changed since the keyboard was sent
    if selected not in BUILDINGS:
        await state.set_state(JoinChat.selecting_building)
        await callback.message.edit_text(
            f"К сожалению, дом {selected} пока не поддерживается. Выберите другой дом:",
            reply_markup=build_building_keyboard().as_markup()
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

        # Offer an invite per chat, skipping the ones the user is already in
        async def describe_chat_access(chat_id: int, emoji: str, title: str, already_in_text: str) -> str:
            if await is_user_in_chat(chat_id, telegram_id):
                return already_in_text
            invite_link = await create_one_time_invite_link(chat_id)
            if invite_link:
                return f"{emoji} {title}: {invite_link}"
            return (
                f"Не удалось создать ссылку на «{title}». "
                "Попробуйте позже или обратитесь к разработчику @xmlChay (Илья)"
            )

        lines = [f"Готово! Дом {building}, квартира {flat_number}."]

        building_chat_id = resolve_building_chat_id(building)
        if building_chat_id is None:
            lines.append(
                f"Чат дома {building} пока не подключен. Как только он появится, "
                "вы сможете получить приглашение по команде /start"
            )
        else:
            lines.append(await describe_chat_access(
                building_chat_id,
                "🏠",
                f"Чат дома {building}",
                f"Вы уже состоите в чате дома {building}, приглашение не требуется."
            ))

        if PUBLIC_CHAT_ID is not None:
            lines.append(await describe_chat_access(
                PUBLIC_CHAT_ID,
                "🏘",
                "Общий чат ЖК",
                "Вы уже состоите в общем чате ЖК, приглашение не требуется."
            ))

        await finish("\n\n".join(lines))

    except Exception as e:
        logging.error(f"Error storing user data: {e}")
        await message.answer(
            "Произошла ошибка при сохранении данных. Пожалуйста, обратитесь к разработчику @xmlChay (Илья)"
        )


# Message: invalid flat number → re-prompt
@dp.message(JoinChat.awaiting_flat_number)
async def on_flat_number_invalid(message: Message):
    await message.answer("Пожалуйста, укажите корректный номер квартиры")


# /flat: show users bound to a flat (connected chats, admins only)
@dp.message(Command("flat"))
async def handle_flat_command(message: Message):
    # Restrict to connected chats; in the shared chat the search covers every building
    if not is_connected_chat(message.chat.id):
        return

    if not await can_use_admin_commands(message):
        return

    building = resolve_chat_building(message.chat.id)

    # Parse flat number from command arguments
    # Expected formats:
    #   /flat 123
    #   /flat@bot 123
    args_text = (message.text or message.caption or "").split(maxsplit=1)
    if len(args_text) < 2 or not args_text[1].strip().isdigit():
        await message.answer("Укажите номер квартиры: например, /flat 123")
        return

    flat_number = args_text[1].strip()

    try:
        query = supabase.table("users").select("*").eq("flat_number", flat_number)
        if building is not None:
            query = query.eq("building", building)
        result = query.execute()

        if not result.data:
            lines = ["Данные не найдены в базе", ""]
            if building is not None:
                lines.append(f"Дом: {building}")
            lines.append(f"Квартира: {flat_number}")
            await message.answer("\n".join(lines))
            return

        # Build message similar to join notification but for flat lookup
        lines = ["ℹ️ Данные по квартире", ""]
        if building is not None:
            lines.append(f"Дом: {building}")
        lines.append(f"Квартира: {flat_number}")
        lines.append("")
        lines.append("Пользователи:")

        records = result.data
        if building is None:
            records = sorted(records, key=lambda rec: str(rec.get("building") or ""))

        for rec in records:
            user_id = rec.get("telegram_id")
            username = rec.get("username") or "Unknown"
            first_name = rec.get("first_name") or "Unknown"
            last_name = rec.get("last_name") or ""
            user_line = (
                f"@{username if username != 'Unknown' else '—'} (ID: {user_id})\n"
                f"Имя: {first_name} {last_name}".strip()
            )
            if building is None:
                user_line += f"\nДом: {rec.get('building') or '—'}"
            lines.append(user_line)
            lines.append("")

        await message.answer("\n".join(lines).strip())
    except Exception as e:
        logging.error(f"/flat: error fetching data: {e}")
        await message.answer("Произошла ошибка при получении данных. Попробуйте позже или обратитесь к разработчику @xmlChay (Илья)")


# /kick: remove a user from the chat by Telegram ID (connected chats, admins only)
@dp.message(Command("kick"))
async def handle_kick_command(message: Message):
    # Restrict to connected chats
    if not is_connected_chat(message.chat.id):
        return

    if not await can_use_admin_commands(message):
        return

    chat_title = resolve_chat_title(message.chat.id)

    # Parse Telegram ID from command arguments
    # Expected formats:
    #   /kick 123456789
    #   /kick@bot 123456789
    args_text = (message.text or message.caption or "").split(maxsplit=1)
    if len(args_text) < 2 or not args_text[1].strip().isdigit():
        await answer_admin_privately(message, "Укажите Telegram ID пользователя: например, /kick 123456789")
        return

    target_id = int(args_text[1].strip())

    if target_id == bot.id:
        await answer_admin_privately(message, "Я не могу исключить самого себя")
        return

    try:
        target = await bot.get_chat_member(chat_id=message.chat.id, user_id=target_id)
    except Exception as err:
        logging.info(f"/kick: cannot get member {target_id} in chat {message.chat.id}: {err}")
        await answer_admin_privately(message, f"{chat_title}: пользователь с ID {target_id} не найден")
        return

    target_status = getattr(target, "status", None)
    if target_status in ["left", "kicked"]:
        await answer_admin_privately(message, f"{chat_title}: пользователь с ID {target_id} не состоит в чате")
        return
    if target_status in ["administrator", "creator"]:
        await answer_admin_privately(
            message,
            f"{chat_title}: нельзя исключить администратора, сначала снимите с него права"
        )
        return

    target_name = format_user_name(target.user)

    try:
        # Ban and immediately unban, so the user is removed but can return later
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id)
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=target_id, only_if_banned=True)
    except Exception as err:
        logging.error(f"/kick: failed to remove user {target_id} from chat {message.chat.id}: {err}")
        await answer_admin_privately(
            message,
            "Не удалось исключить пользователя. Проверьте, что у бота есть право удалять участников"
        )
        return

    logging.info(f"/kick: user {target_name} (ID: {target_id}) removed from chat {message.chat.id} ({chat_title}).")
    await answer_admin_privately(message, f"🚫 {chat_title}: пользователь {target_name} исключен")


# Join requests: approve the residents the bot has already verified, leave the rest to admins
@dp.chat_join_request()
async def on_chat_join_request(request: ChatJoinRequest):
    if not is_connected_chat(request.chat.id):
        return

    chat_title = resolve_chat_title(request.chat.id)
    user_name = format_user_name(request.from_user)
    user_id = request.from_user.id

    # A building chat is only for residents of that building; the shared chat is for anyone registered
    building = resolve_chat_building(request.chat.id)
    if not has_user_record(user_id, building):
        logging.info(
            f"Join request from {user_name} (ID: {user_id}) to {chat_title} left for manual review: "
            f"no matching record in the database"
        )
        return

    try:
        await bot.approve_chat_join_request(chat_id=request.chat.id, user_id=user_id)
        logging.info(f"Approved join request from {user_name} (ID: {user_id}) to {chat_title}")
    except Exception as err:
        logging.error(f"Failed to approve join request from {user_id} to {chat_title}: {err}")


# Chat member update handler - detect when users leave the group
@dp.chat_member()
async def on_chat_member_update(update: ChatMemberUpdated):
    # Only process updates for the building chats and the shared complex chat
    if not is_connected_chat(update.chat.id):
        logging.info(
            f"Ignoring chat_member update from unconfigured chat {update.chat.id} "
            f"({update.chat.title}); check GROUP_CHAT_IDS and PUBLIC_CHAT_ID"
        )
        return
    
    # Check if user is no longer in the chat, whether they left or were removed
    if update.old_chat_member.status in ["member", "administrator", "creator"] and update.new_chat_member.status in ["left", "kicked"]:
        building = resolve_chat_building(update.chat.id)

        # The affected user is the one in new_chat_member
        user_id = update.new_chat_member.user.id
        user_name = format_user_name(update.new_chat_member.user)
        first_name = update.new_chat_member.user.first_name or "Сосед"
        
        try:
            # Data is kept only while the user takes part in at least one connected chat
            still_in_some_chat = False
            for chat_id in all_connected_chat_ids():
                if chat_id != update.chat.id and await is_user_in_chat(chat_id, user_id):
                    still_in_some_chat = True
                    break

            # Leaving the shared chat costs nothing while the user stays in a building chat
            if still_in_some_chat and building is None:
                return

            select_query = supabase.table("users").select("*").eq("telegram_id", user_id)
            delete_query = supabase.table("users").delete().eq("telegram_id", user_id)
            if still_in_some_chat:
                select_query = select_query.eq("building", building)
                delete_query = delete_query.eq("building", building)

            user_flats = select_query.execute()
            if not user_flats.data:
                return

            delete_query.execute()
            flats_count = len(user_flats.data)

            logging.info(
                f"User {user_name} (ID: {user_id}) is no longer in chat {update.chat.id} "
                f"({resolve_chat_title(update.chat.id)}). Removed {flats_count} flat(s) from database."
            )

            if still_in_some_chat:
                left_text = f"вы больше не состоите в чате соседей по дому {building}"
                data_text = f"Ваши данные по этому дому ({flats_count} квартир(а))"
            else:
                left_text = "вы больше не состоите в чатах ЖК"
                data_text = f"Все ваши данные ({flats_count} квартир(а))"

            # Notify user in private message about data deletion
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"👋 {first_name}, {left_text}.\n\n"
                         f"{data_text} были удалены из базы данных "
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
        user_id = update.new_chat_member.user.id
        display_name = format_user_name(update.new_chat_member.user)

        # Track registrations so admins can spot joins made outside the bot
        try:
            registration_query = supabase.table("users").select("id").eq("telegram_id", user_id)
            if building is not None:
                registration_query = registration_query.eq("building", building)
            if not registration_query.execute().data:
                logging.warning(
                    f"User {display_name} (ID: {user_id}) joined {resolve_chat_title(update.chat.id)} "
                    f"({update.chat.id}) without a record in the database."
                )
        except Exception as e:
            logging.error(f"Error checking registration of joined user: {e}")

        try:
            await bot.send_message(
                chat_id=update.chat.id,
                text=(
                    f"✅ Пользователь {display_name} присоединился(-ась) к чату\n\n"
                    "Добро пожаловать! Пожалуйста, уважайте своих соседей и не используйте чат для рекламы"
                )
            )
        except Exception as e:
            logging.error(f"Error welcoming user in chat {update.chat.id} ({resolve_chat_title(update.chat.id)}): {e}")


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

    # Try to remove the user from every connected chat, including the shared one
    removed_from = 0
    for chat_id in all_connected_chat_ids():
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
    logging.info(
        "Effective configuration: buildings=%s, building chats=%s, public chat=%s, owners=%s",
        BUILDINGS, GROUP_CHAT_IDS, PUBLIC_CHAT_ID, sorted(OWNER_IDS)
    )
    await dp.start_polling(bot)

keep_alive()
if __name__ == '__main__':
    asyncio.run(main())