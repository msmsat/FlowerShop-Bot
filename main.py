# flower_shop_bot_vibe.py
# Телеграм-бот интернет-магазина цветов (aiogram v3)
# Установите: pip install aiogram aiosqlite

import asyncio
import os

import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from datetime import datetime
import random
from dotenv import load_dotenv
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import aiohttp
import payment_services

load_dotenv()

# --------- Настройки (подставьте ваш токен) ---------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
CRYPTOPAY_TOKEN = os.getenv("CRYPTOPAY_TOKEN")
PORTMONE_TOKEN = os.getenv("PORTMONE_TOKEN")
if not BOT_TOKEN: exit("Error: BOT_TOKEN not found in environment variables!")
# ----------------------------------------------------

class OrderState(StatesGroup):
    waiting_for_address = State()  # Ждем ввод адреса
    waiting_for_time = State()     # Ждем ввод времени
    waiting_for_payment_type = State()  # <--- Важно!

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_PATH = "flower_shop.db"

# --------- SQL и инициализация БД ---------
CREATE_PRODUCTS_TABLE = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    price INTEGER NOT NULL,
    description TEXT,
    type TEXT NOT NULL
);
"""

CREATE_CART_TABLE = """
CREATE TABLE IF NOT EXISTS cart (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, product_id)
);
"""

CREATE_DRAFT_TABLE = """
CREATE TABLE IF NOT EXISTS bouquet_draft (
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, product_id)
);
"""

INITIAL_PRODUCTS = [
    ("Розы", 220, "🌹 Классические красные розы. Символ страсти и любви.", "lonely",
     "https://i.pinimg.com/736x/a1/b1/f5/a1b1f520076d41d57fffa1a97b2432fa.jpg"),

    ("Тюльпаны", 180, "🌷 Весенние тюльпаны. Нежность и свежесть.", "lonely",
     "https://i.pinimg.com/736x/2f/80/12/2f8012ee7b7e649aeea32b472e97d669.jpg"),

    ("Лилии", 195, "🌿 Ароматные белые лилии. Благородство и чистота.", "lonely",
     "https://i.pinimg.com/736x/04/d2/2a/04d22ad42556fbc8958454b91cc52f34.jpg"),

    ("10 роз букет", 2100, "💐 Классический букет из 10 красных роз.", "bouquet",
     "https://i.pinimg.com/736x/2d/50/bd/2d50bdb45a4a90734c96615b3e5577eb.jpg"),

    ("Белые розы", 230, "🤍 Белоснежные розы для самых искренних чувств.", "lonely",
     "https://i.pinimg.com/736x/30/63/cd/3063cd2f13c5d5656c7dec0d251bf50b.jpg"),

    ("Ранункулюсы", 210, "🌸 Воздушные ранункулюсы, похожие на пионы.", "lonely",
     "https://i.pinimg.com/736x/8e/0a/e3/8e0ae31cb1071b9ae0a4f7c7f05005b0.jpg"),

    ("Дикие розы", 240, "🍃 Кустовые розы. Ощущение дикого сада.", "lonely",
     "https://i.pinimg.com/736x/38/f0/46/38f04649b917733f1e700acba85eaa17.jpg"),

    ("Авторский микс", 4200, "✨ Большой сборный букет из разных цветов.", "bouquet",
     "https://i.pinimg.com/736x/c4/42/80/c442805bebc631f901c16eba044b656d.jpg")
]


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_PRODUCTS_TABLE)
        await db.execute(CREATE_CART_TABLE)
        await db.execute(CREATE_DRAFT_TABLE)

        # --- Миграция: добавляем колонку image, если её нет ---
        try:
            await db.execute("ALTER TABLE products ADD COLUMN image TEXT")
        except Exception:
            pass  # Колонка уже есть

        # Заполняем товары (или обновляем ссылки, если товары есть)
        for name, price, desc, type_f, img in INITIAL_PRODUCTS:
            # Пытаемся вставить новый
            try:
                await db.execute(
                    "INSERT INTO products (name, price, description, type, image) VALUES (?, ?, ?, ?, ?)",
                    (name, price, desc, type_f, img)
                )
            except Exception:
                # Если товар с таким именем есть — обновляем ему картинку
                await db.execute(
                    "UPDATE products SET image = ? WHERE name = ?",
                    (img, name)
                )

        await db.commit()

# --------- Утилиты для работы с БД ---------
async def get_all_products():
    async with aiosqlite.connect(DB_PATH) as db:
        # Добавили image в выборку
        cur = await db.execute("SELECT id, name, price, description, type, image FROM products ORDER BY id")
        return await cur.fetchall()

async def get_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name, price, description, type FROM products WHERE id = ?", (product_id,))
        return await cur.fetchone()

async def add_to_cart(user_id: int, product_id: int, qty: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        # если запись существует — обновляем количество
        cur = await db.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        row = await cur.fetchone()
        if row:
            new_q = row[0] + qty
            await db.execute("UPDATE cart SET quantity = ? WHERE user_id = ? AND product_id = ?", (new_q, user_id, product_id))
        else:
            await db.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, ?)", (user_id, product_id, qty))
        await db.commit()

async def remove_one_from_cart(user_id: int, product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        row = await cur.fetchone()
        if not row:
            return
        q = row[0]
        if q > 1:
            await db.execute("UPDATE cart SET quantity = ? WHERE user_id = ? AND product_id = ?", (q - 1, user_id, product_id))
        else:
            await db.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        await db.commit()

async def clear_cart(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_cart(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        # Добавили p.description и p.type в выборку
        cur = await db.execute("""
            SELECT p.id, p.name, p.price, c.quantity, p.description, p.type
            FROM cart c
            JOIN products p ON p.id = c.product_id
            WHERE c.user_id = ?
            ORDER BY p.id
        """, (user_id,))
        return await cur.fetchall()

# --------- Клавиатуры ---------
def build_start_keyboard(products):
    print(products)
    kb = []
    i = 0
    while i < len(products):
        p1 = products[i]
        p2 = products[i + 1] if i + 1 < len(products) else None
        row = [InlineKeyboardButton(text=f"{p1[1]}", callback_data=f"product_{p1[0]}")]
        if p2: row.append(InlineKeyboardButton(text=f"{p2[1]}", callback_data=f"product_{p2[0]}"))
        kb.append(row)
        controls = [InlineKeyboardButton(text="-", callback_data=f"minus_bouquet_{p1[0]}"), InlineKeyboardButton(text="+", callback_data=f"plus_bouquet_{p1[0]}")]
        if p2: controls.extend([InlineKeyboardButton(text="-", callback_data=f"minus_bouquet_{p2[0]}"), InlineKeyboardButton(text="+", callback_data=f"plus_bouquet_{p2[0]}")])
        kb.append(controls)
        i += 2
    return InlineKeyboardMarkup(inline_keyboard=kb)



def product_detail_kb(product_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить в корзину", callback_data=f"add_{product_id}")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu"), InlineKeyboardButton(text="🧺 Корзина", callback_data="view_cart")]
    ])
    return kb


def cart_kb(cart_items):
    # cart_items: list of (id, name, price, qty, description, type)
    kb_rows = []
    for pid, name, price, qty, desc, p_type in cart_items:
        row = []
        # Кнопка удаления
        row.append(InlineKeyboardButton(text=f"❌ Удалить «{name}»", callback_data=f"remove_{pid}"))

        # Если это собранный букет — добавляем кнопку изменения
        if p_type == "created_bouquet":
            row.append(InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_bouquet_{pid}"))

        kb_rows.append(row)

    kb_rows.append([InlineKeyboardButton(text="🧹 Очистить корзину", callback_data="clear_cart")])
    kb_rows.append([InlineKeyboardButton(text="✅ Оформить", callback_data="checkout"),
                    InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


async def show_creation_menu(message: Message, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. Получаем текущий черновик
        cur = await db.execute("""
            SELECT p.id, p.name, p.price, d.quantity 
            FROM bouquet_draft d
            JOIN products p ON p.id = d.product_id
            WHERE d.user_id = ?
        """, (user_id,))
        draft_items = await cur.fetchall()

        # 2. Получаем сумму корзины
        cur = await db.execute("""
            SELECT c.quantity, p.price 
            FROM cart c
            JOIN products p ON p.id = c.product_id
            WHERE c.user_id = ?
        """, (user_id,))
        cart_rows = await cur.fetchall()
        cart_total = sum(qty * price for qty, price in cart_rows)

    # Считаем сумму текущего букета
    draft_lines = []
    draft_total = 0
    for _, name, price, qty in draft_items:
        summ = price * qty
        draft_lines.append(f"{name} — {price} ₽ × {qty} = {summ} ₽")
        draft_total += summ

    grand_total = draft_total + cart_total

    text = (
        "🌸 <b>Конструктор букета</b>\n\n"
        "Нажимайте на кнопки, чтобы собрать свой идеальный состав. "
        "Как закончите — нажмите «Упаковать», и букет отправится в общую корзину. 👇\n"
        "〰〰〰〰〰〰〰〰〰\n"
    )

    if draft_lines:
        text += "\n".join(draft_lines)
        text += f"\n\n💐 <b>Итог этого букета: {draft_total} ₽</b>"
    else:
        text += "<i>(Пока цветов не выбрано)</i>\n\n💐 <b>Итог этого букета: 0 ₽</b>"

    if cart_total > 0:
        text += f"\n\n🧺 <i>В корзине уже есть товаров на: {cart_total} ₽</i>"
        text += f"\n💰 <b>Общая сумма заказа: {grand_total} ₽</b>"
    else:
        text += f"\n\n💰 <b>Общая сумма заказа: {grand_total} ₽</b>"

    # Кнопки
    all_products = await get_all_products()
    bouquets = [p for p in all_products if p[4] == "lonely"]
    kb = []
    for pid, name, price, *_ in bouquets:
        kb.append([InlineKeyboardButton(text=f"🔍 {name} — {price} ₽", callback_data=f"view_flower_{pid}")])

        kb.append([
            InlineKeyboardButton(text="+1", callback_data=f"bq_add_{pid}_1"),
            InlineKeyboardButton(text="+10", callback_data=f"bq_add_{pid}_10"),
            InlineKeyboardButton(text="-1", callback_data=f"bq_sub_{pid}_1"),
            InlineKeyboardButton(text="🗑", callback_data=f"bq_del_{pid}")
        ])

    kb.append([InlineKeyboardButton(text="🎁 Упаковать (+15₽) и в корзину", callback_data="pack_yes"),
               InlineKeyboardButton(text="🚫 В корзину без упаковки", callback_data="pack_no")])
    kb.append([InlineKeyboardButton(text="🧹 Сбросить всё", callback_data="reset_draft")])

    # Кнопка назад / сохранить
    if user_id in user_states and 'editing_pid' in user_states[user_id]:
        kb.append([InlineKeyboardButton(text="💾 Сохранить и выйти", callback_data="back_from_creation")])
    else:
        kb.append([InlineKeyboardButton(text="🔙 Назад (без сохранения)", callback_data="back_from_creation")])

    # --- ИСПРАВЛЕНИЕ: Умная отправка ---
    try:
        # Сначала пробуем просто отредактировать текст (это работает для кнопок + и -)
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception:
        # Если не вышло (например, там была КАРТИНКА), то удаляем старое и шлем новое
        try:
            await message.delete()
        except:
            pass # Если уже удалено
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

# --- Универсальная функция завершения заказа ---
async def finalize_order(message: Message, state: FSMContext, user_id: int, payment_label: str, end_text: str):
    user = message.from_user

    # 1. Генерируем ID заказа (например, случайные цифры + ID юзера)
    # Это позволит уникально идентифицировать заказ
    order_ref = f"{random.randint(100, 999)}-{user_id}"

    # 2. Достаем данные из State
    data = await state.get_data()
    address = data.get("temp_address", "Не указан")
    delivery_time = data.get("delivery_time", "Не указано")

    # 3. Достаем корзину
    items = await get_cart(user_id)
    if not items:
        await message.answer("Ошибка: Корзина пуста. Если вы оплатили заказ, пожалуйста, перешлите чек флористу.")
        return

    # 4. Считаем итог
    total_price = 0
    cart_text = ""
    for _, name, price, qty, desc, p_type in items:
        summ = price * qty
        total_price += summ
        cart_text += f"▫️ {name} x {qty} = {summ} ₽\n"
        if p_type == "created_bouquet":
            cart_text += f"   <i>(Состав: {desc[:50]}...)</i>\n"

    # 5. Отчет Админу (Добавили ID заказа!)
    admin_report = (
        f"🚨 <b>НОВЫЙ ЗАКАЗ #{order_ref}</b>\n"
        f"👤 Клиент: <a href='tg://user?id={user_id}'>{user.full_name}</a> (@{user.username})\n"
        f"🆔 ID заказа: <code>{order_ref}</code>\n"
        f"📍 <b>Адрес:</b> {address}\n"
        f"⏰ <b>Время:</b> {delivery_time}\n"
        f"💰 <b>Тип оплаты:</b> {payment_label}\n"
        f"〰〰〰〰〰〰〰\n"
        f"{cart_text}"
        f"〰〰〰〰〰〰〰\n"
        f"💰 <b>ИТОГО: {total_price} ₽</b>"
    )
    if ADMIN_ID:
        try: await bot.send_message(ADMIN_ID, admin_report, parse_mode="HTML")
        except Exception as e: print(f"Ошибка отправки админу: {e}")

    # 6. Очистка
    await clear_cart(user_id)
    await state.clear()

    # 7. Ответ пользователю (Добавили контакты и ID)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌸 В главное меню", callback_data="main_menu")]
    ])
    florist_contact = "@matvey_sadovsky"
    await message.answer(
        f"🎉 <b>Ваш заказ #{order_ref} принят!</b>\n\n"
        f"Способ оплаты: <i>{payment_label}</i>\n"
        f"Адрес: <i>{address}</i>\n"
        f"Время: <i>{delivery_time}</i>\n\n"
        f"{end_text}\n"
        f"〰〰〰〰〰〰〰\n"
        f"📞 <b>Контакты:</b>\n"
        f"Если вы хотите изменить или отменить заказ, напишите нам: {florist_contact}\n"
        f"Обязательно укажите номер заказа: <code>{order_ref}</code>",
        reply_markup=kb,
        parse_mode="HTML"
    )

# --- ПРОВЕРКА КРИПТЫ ---
@dp.callback_query(F.data.startswith("check_pay_crypto_"))
async def check_crypto_payment(call: CallbackQuery, state: FSMContext):
    invoice_id = call.data.split("_")[3]
    status = await payment_services.check_crypto_invoice_status(invoice_id)
    # 2. Проверяем, что статус именно 'paid'
    if status == 'paid':
        await call.answer("✅ Оплата получена!")
        await finalize_order(call.message, state, call.from_user.id, "💎 CryptoBot (Оплачено)", "Заказ оплачен онлайн. Спасибо! 🤝")
    else: await call.answer("❌ Оплата еще не видна. Подождите минуту.", show_alert=True)


# --- ПРОВЕРКА ПЕРЕД ОПЛАТОЙ (Pre-Checkout) ---
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Здесь можно добавить проверку наличия товара в БД, если нужно.
    # ok=True означает, что мы разрешаем транзакцию.
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    return

# --- УСПЕШНАЯ ОПЛАТА (Successful Payment) ---
@dp.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext):
    user_id = message.from_user.id
    payment_info = message.successful_payment
    total_amount = payment_info.total_amount / 100  # Переводим копейки обратно в валюту
    currency = payment_info.currency
    
    # Формируем красивый текст для админа
    payment_label = f"💳 Portmone (Оплачено: {total_amount} {currency})"
    end_text = "Оплата прошла успешно! Мы уже начали собирать ваш букет. 💐"
    await finalize_order(message, state, user_id, payment_label, end_text)

# Выбор оплаты
@dp.callback_query(OrderState.waiting_for_payment_type)
async def process_payment_selection(call: CallbackQuery, state: FSMContext):
    payment_type = call.data
    user = call.from_user
    user_id = user.id

    # Если нажали "Назад"
    if payment_type == "back_to_pay_choice":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Криптовалюта (USDT)", callback_data="pay_crypto")],
            [InlineKeyboardButton(text="🟠 Portmone (UAH)", callback_data="pay_portmone")],
            [InlineKeyboardButton(text="💵 На месте", callback_data="pay_onsite")]
        ])
        await call.message.edit_text("Выберите удобный способ оплаты:", reply_markup=kb)
        return

    # Получаем товары
    items = await get_cart(user_id)
    if not items:
        await call.answer("Корзина пуста!", show_alert=True)
        return
    total_price = sum(price * qty for _, _, price, qty, _, _ in items)

    # --- 1. КРИПТОВАЛЮТА ---
    if payment_type == "pay_crypto":
        await call.message.edit_text("⏳ Создаем счет в CryptoBot...")
        amount_usdt = round(total_price, 2)

        # !!! ИСПРАВЛЕНИЕ НИЖЕ !!!
        # Распаковываем 3 значения, которые возвращает payment_services.py
        full_json, invoice_id, invoice_url = await payment_services.create_crypto_invoice(
            amount_usdt, f"Order {user_id}", str(user_id)
        )
        if not invoice_url:
            await call.message.edit_text("Ошибка создания счета CryptoBot.")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"👉 Оплатить {amount_usdt} RUB", url=invoice_url)],
            [InlineKeyboardButton(text="🔄 Я оплатил", callback_data=f"check_pay_crypto_{invoice_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_pay_choice")]
        ])

        await call.message.edit_text(f"💎 <b>Оплата CryptoBot</b>\nСумма: {amount_usdt} RUB", reply_markup=kb, parse_mode="HTML")
        return

    # --- 2. PORTMONE (Telegram Payments) ---
    if payment_type == "pay_portmone":
        if not PORTMONE_TOKEN:
            await call.answer("Ошибка: Токен оплаты не настроен", show_alert=True)
            return
        await call.message.delete()
        await call.message.answer("⏳ Формируем счет...")

        # Цена в копейках (total_price * 100)
        price_amount = total_price * 100
        prices = [LabeledPrice(label="Заказ цветов", amount=price_amount)]
        payload = f"order_{user_id}_{int(datetime.now().timestamp())}"

        await bot.send_invoice(
            chat_id=call.message.chat.id,
            title="Оплата заказа",
            description=f"Заказ цветов для {user.full_name}. Сумма: {total_price} UAH",
            payload=payload,
            provider_token=PORTMONE_TOKEN,
            currency="UAH",
            prices=prices,
            start_parameter=f"pay_{user_id}",
            need_shipping_address=False,
            is_flexible=False
        )
        return

    if payment_type == "pay_onsite":
        payment_label = "💵 На месте"
        end_text = "Оплата курьеру при получении. ❤️"
    else:
        await call.answer()
        return
    await finalize_order(call.message, state, user_id, payment_label, end_text)

user_states = {}

# --- КНОПКА ОТМЕНЫ (на любом этапе) ---
@dp.callback_query(F.data == "cancel_order")
async def cancel_fsm(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    await state.clear()
    items = await get_cart(user_id)
    if not items:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]])
        await call.message.edit_text("🧺 Ваша корзина пуста — время добавить немного цветов!", reply_markup=kb)
        await call.answer()
        return
    # Формируем текст корзины
    lines = []
    total = 0
    for pid, name, price, qty, desc, p_type in items:
        summ = price * qty
        total += summ
        # Основная строка
        item_text = f"🔹 <b>{name}</b>\n     {price} ₽ × {qty} шт. = {summ} ₽"
        # Если это авторский букет, добавляем состав (он лежит в description)
        if p_type == "created_bouquet" and desc:
            # Убираем "Состав: " для красоты, если оно там есть, и делаем курсивом
            clean_desc = desc.replace("Состав: ", "").strip()
            item_text += f"\n     <i>└ {clean_desc}</i>"
        lines.append(item_text)
    
    text = "<b>🧺 Ваша корзина:</b>\n\n" + "\n\n".join(lines) + f"\n\n💰 Итого к оплате: <b>{total} ₽</b>\n\nМы приготовим всё красиво и аккуратно — осталось оформить."
    await call.message.edit_text(text, reply_markup=cart_kb(items), parse_mode="HTML")
    return

# --- НОВЫЙ ХЭНДЛЕР ДЛЯ ОФОРМЛЕНИЯ (Вставить ПЕРЕД generic_callback) ---
@dp.callback_query(F.data == "checkout")
async def start_checkout_handler(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    items = await get_cart(user_id)
    if not items:
        await call.answer(text="Корзина пуста 😔", show_alert=True)
        return
    await state.set_state(OrderState.waiting_for_address)

    # 3. Клавиатура (добавил кнопку Отмена, чтобы можно было выйти)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_order")]
    ])
    await call.message.edit_text(
        "🎉 <b>Оформление заказа</b>\n\n"
        "Пожалуйста, напишите <b>адрес доставки</b> (Улица, дом, квартира, подъезд). 👇",
        reply_markup=kb, parse_mode="HTML")
    await call.answer()


# --- 1. Обновляем команду /start ---
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    all_products = await get_all_products()
    bouquets = [p for p in all_products if p[4] == "bouquet"]

    kb = []
    # Кнопки теперь ведут на просмотр (view_product_)
    for pid, name, price, desc, p_type, _ in bouquets:
        kb.append([InlineKeyboardButton(text=f"👁 {name} — {price} ₽", callback_data=f"view_product_{pid}")])

    kb.append([InlineKeyboardButton(text="🌸 Создать свой букет", callback_data="create_bouquet")])
    kb.append([InlineKeyboardButton(text="🧺 Перейти в корзину", callback_data="view_cart")])

    await message.answer(
        "🌿 <b>Bloom & Vibe</b>\n\nНажмите на название букета, чтобы увидеть фото и описание. 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML"
    )

@dp.callback_query()
async def generic_callback(call: CallbackQuery, state: FSMContext):
    data = call.data or ""
    user_id = call.from_user.id

    # Вернуться в главное меню (ТОТ ЖЕ НОВЫЙ ДИЗАЙН)
    if data == "main_menu":
        # Сначала пробуем удалить старое сообщение (если это была картинка)
        try:
            await call.message.delete()
        except:
            pass  # Если не получилось удалить (уже удалено), просто шлем новое

        all_products = await get_all_products()
        bouquets = [p for p in all_products if p[4] == "bouquet"]

        kb = []
        for pid, name, price, desc, p_type, _ in bouquets:
            kb.append([InlineKeyboardButton(text=f"👁 {name} — {price} ₽", callback_data=f"view_product_{pid}")])

        kb.append([InlineKeyboardButton(text="🌸 Создать свой букет", callback_data="create_bouquet")])
        kb.append([InlineKeyboardButton(text="🧺 Перейти в корзину", callback_data="view_cart")])

        await call.message.answer(
            "🌿 <b>Bloom & Vibe</b>\n\nНажмите на название букета, чтобы увидеть фото и описание. 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode="HTML"
        )
        return

    if data.startswith("view_flower_"):
        try:
            pid = int(data.split("_")[2])
        except:
            await call.answer()
            return

        try:
            await call.message.delete()
        except:
            pass

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT name, price, description, image FROM products WHERE id = ?", (pid,))
            row = await cur.fetchone()

        if row:
            name, price, desc, img_url = row
            # Запасная картинка, если в базе пусто
            if not img_url:
                img_url = "https://images.unsplash.com/photo-1562690868-60bbe7293e94"

            caption = f"🌺 <b>{name}</b>\n\n{desc}\n\n💰 Цена за шт: <b>{price} ₽</b>"

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к сборке", callback_data="resume_creation")]
            ])

            # Пытаемся отправить фото. Если ссылка плохая — шлем заглушку.
            try:
                await call.message.answer_photo(photo=img_url, caption=caption, reply_markup=kb, parse_mode="HTML")
            except Exception:
                # Если конкретная картинка (например, Ранункулюсов) не грузится
                fallback_url = "https://images.unsplash.com/photo-1562690868-60bbe7293e94"
                await call.message.answer_photo(photo=fallback_url, caption=caption, reply_markup=kb, parse_mode="HTML")

        await call.answer()
        return

    # Просмотр товара (Карточка товара)
    if data.startswith("view_product_"):
        try:
            pid = int(data.split("_")[2])
        except:
            return

        # Удаляем предыдущее меню
        try:
            await call.message.delete()
        except:
            pass

        async with aiosqlite.connect(DB_PATH) as db:
            # --- ИСПРАВЛЕНИЕ: Добавили image в запрос ---
            cur = await db.execute("SELECT name, price, description, image FROM products WHERE id = ?", (pid,))
            row = await cur.fetchone()

        if not row:
            await call.answer("Товар не найден", show_alert=True)
            return

        # --- ИСПРАВЛЕНИЕ: Распаковываем 4 значения ---
        name, price, desc, img_url = row

        # Если вдруг картинки нет в базе, ставим запасную
        if not img_url:
            img_url = "https://images.unsplash.com/photo-1562690868-60bbe7293e94?auto=format&fit=crop&w=1000&q=80"

        caption = f"💐 <b>{name}</b>\n\n<i>{desc}</i>\n\n💰 <b>Цена: {price} ₽</b>"

        # --- КЛАВИАТУРА ---
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="➖ С корзины", callback_data=f"remove_from_view_{pid}"),
                InlineKeyboardButton(text="➕ В корзину", callback_data=f"add_from_view_{pid}")
            ],
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="main_menu")]
        ])

        # Отправляем фото
        try:
            await call.message.answer_photo(photo=img_url, caption=caption, reply_markup=kb, parse_mode="HTML")
        except Exception:
            # Если ссылка Pinterest не грузится, пробуем запасную
            fallback = "https://images.unsplash.com/photo-1562690868-60bbe7293e94?auto=format&fit=crop&w=1000&q=80"
            await call.message.answer_photo(photo=fallback, caption=caption, reply_markup=kb, parse_mode="HTML")

        return

    # Удаление из режима просмотра (кнопка Минус)
    if data.startswith("remove_from_view_"):
        try:
            pid = int(data.split("_")[3])
        except:
            return

        # 1. Удаляем 1 штуку (используем готовую функцию)
        await remove_one_from_cart(user_id, pid)

        # 2. Узнаем, сколько осталось (чтобы красиво написать)
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, pid))
            row = await cur.fetchone()
            new_qty = row[0] if row else 0

        # 3. Показываем уведомление
        if new_qty > 0: await call.answer(f"➖ Убрали. Осталось: {new_qty} шт.", show_alert=False)
        else: await call.answer("🗑 Товар полностью удален из корзины", show_alert=False)
        return

    # Добавление из режима просмотра
    if data.startswith("add_from_view_"):
        try:
            pid = int(data.split("_")[3])
        except:
            return

        # 1. Добавляем товар
        await add_to_cart(user_id, pid, 1)

        # 2. Узнаем, сколько их теперь стало
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, pid))
            row = await cur.fetchone()
            new_qty = row[0] if row else 0

        # 3. Пишем количество в уведомлении
        await call.answer(f"✅ Добавлено! Теперь в корзине: {new_qty} шт.", show_alert=False)
        return

    if data == "create_bouquet":
        # Если пользователь нажал кнопку "Создать букет" в меню — он хочет новый.
        # Поэтому мы принудительно очищаем черновик.
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM bouquet_draft WHERE user_id = ?", (user_id,))
            await db.commit()

        # Также сбрасываем состояние редактирования, если оно вдруг зависло
        if user_id in user_states:
            user_states[user_id].pop('editing_pid', None)

        await show_creation_menu(call.message, user_id)
        return

    # Продолжить сборку (вернуться, не удаляя черновик)
    if data == "resume_creation":
        await show_creation_menu(call.message, user_id)
        return

    if data == "back_from_creation":
        async with aiosqlite.connect(DB_PATH) as db:
            # СЦЕНАРИЙ 1: Мы РЕДАКТИРОВАЛИ существующий букет
            if user_id in user_states and 'editing_pid' in user_states[user_id]:
                old_pid = user_states[user_id]['editing_pid']

                cur = await db.execute("""
                        SELECT p.name, p.price, d.quantity 
                        FROM bouquet_draft d JOIN products p ON p.id = d.product_id 
                        WHERE d.user_id = ?
                    """, (user_id,))
                items = await cur.fetchall()

                if not items:
                    await db.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, old_pid))
                    await call.answer("Пустой букет удален")
                else:
                    total_price = 0
                    desc_parts = []
                    for name, price, qty in items:
                        total_price += price * qty
                        desc_parts.append(f"{name} ({qty})")

                    final_desc = f"Состав: {', '.join(desc_parts)}."

                    await db.execute(
                        "UPDATE products SET price = ?, description = ? WHERE id = ?",
                        (total_price, final_desc, old_pid)
                    )
                    await call.answer("Изменения сохранены! ✅")

                del user_states[user_id]['editing_pid']

            # СЦЕНАРИЙ 2: Мы создавали НОВЫЙ букет
            else:
                await call.answer("Черновик удален 🗑")

            await db.execute("DELETE FROM bouquet_draft WHERE user_id = ?", (user_id,))
            await db.commit()

        # --- ИСПРАВЛЕНИЕ: Добавили _ для приема картинки ---
        all_products = await get_all_products()
        bouquets = [p for p in all_products if p[4] == "bouquet"]

        kb = []
        # ТЕПЕРЬ ТУТ 6 ПЕРЕМЕННЫХ (добавлено _)
        for pid, name, price, desc, p_type, _ in bouquets:
            kb.append([InlineKeyboardButton(text=f"👁 {name} — {price} ₽", callback_data=f"view_product_{pid}")])

        kb.append([InlineKeyboardButton(text="🌸 Создать свой букет", callback_data="create_bouquet")])
        kb.append([InlineKeyboardButton(text="🧺 Перейти в корзину", callback_data="view_cart")])

        await call.message.edit_text(
            "🌿 <b>Bloom & Vibe</b>\n\n"
            "Вы вернулись в меню. Нажмите на название букета, чтобы увидеть фото и описание. 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode="HTML"
        )
        return

    if data == "reset_draft":
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM bouquet_draft WHERE user_id = ?", (user_id,))
            await db.commit()

        # Если мы редактировали старый букет и решили сбросить — забываем про редактирование
        if user_id in user_states and 'editing_pid' in user_states[user_id]:
            del user_states[user_id]['editing_pid']

        await show_creation_menu(call.message, user_id)
        await call.answer("Сборка сброшена")
        return

    if data.startswith("bq_"):
        parts = data.split("_")
        action = parts[1]
        try:
            pid = int(parts[2])
        except:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            # --- Логика изменения количества (как и была) ---
            cur = await db.execute("SELECT quantity FROM bouquet_draft WHERE user_id = ? AND product_id = ?",
                                   (user_id, pid))
            row = await cur.fetchone()
            current_qty = row[0] if row else 0

            new_qty = current_qty
            if action == "add":
                new_qty += int(parts[3])
            elif action == "sub":
                new_qty -= int(parts[3])
            elif action == "del":
                new_qty = 0

            if new_qty <= 0:
                await db.execute("DELETE FROM bouquet_draft WHERE user_id = ? AND product_id = ?", (user_id, pid))
            else:
                if row:
                    await db.execute("UPDATE bouquet_draft SET quantity = ? WHERE user_id = ? AND product_id = ?",
                                     (new_qty, user_id, pid))
                else:
                    await db.execute("INSERT INTO bouquet_draft (user_id, product_id, quantity) VALUES (?, ?, ?)",
                                     (user_id, pid, new_qty))
            await db.commit()

            # --- Подготовка данных для чека ---

            # 1. Читаем текущий букет (Draft)
            cur = await db.execute("""
                    SELECT p.name, p.price, d.quantity 
                    FROM bouquet_draft d JOIN products p ON p.id = d.product_id 
                    WHERE d.user_id = ?
                """, (user_id,))
            draft_items = await cur.fetchall()

            # 2. Читаем основную корзину (Cart) для общей суммы
            cur = await db.execute("""
                    SELECT c.quantity, p.price 
                    FROM cart c JOIN products p ON p.id = c.product_id
                    WHERE c.user_id = ?
                """, (user_id,))
            cart_rows = await cur.fetchall()
            cart_total = sum(q * p for q, p in cart_rows)

        # Формируем текст
        lines = []
        draft_total = 0
        for name, price, qty in draft_items:
            s = price * qty
            lines.append(f"{name} — {price} ₽ × {qty} = {s} ₽")
            draft_total += s

        grand_total = draft_total + cart_total

        text = (
            "🌸 <b>Конструктор букета</b>\n\n"
            "Нажимайте на кнопки, чтобы собрать свой идеальный состав. "
            "Как закончите — нажмите «Упаковать», и букет отправится в общую корзину. 👇\n"
            "〰〰〰〰〰〰〰〰〰\n"
        )
        if lines:
            text += "\n".join(lines) + f"\n\n💐 <b>Итог этого букета: {draft_total} ₽</b>"
        else:
            text += "<i>(Пока цветов не выбрано)</i>\n\n💐 <b>Итог этого букета: 0 ₽</b>"

        # Добавляем инфо про корзину
        if cart_total > 0:
            text += f"\n\n🧺 <i>В корзине уже есть товаров на: {cart_total} ₽</i>"
            text += f"\n💰 <b>Общая сумма заказа: {grand_total} ₽</b>"
        else:
            text += f"\n\n💰 <b>Общая сумма заказа: {grand_total} ₽</b>"

        try:
            await call.message.edit_text(text, reply_markup=call.message.reply_markup, parse_mode="HTML")
        except:
            pass
        await call.answer()
        return

    if data in ["pack_yes", "pack_no"]:
        async with aiosqlite.connect(DB_PATH) as db:
            # 1. Достаем черновик
            cur = await db.execute("""
                SELECT p.name, p.price, d.quantity 
                FROM bouquet_draft d JOIN products p ON p.id = d.product_id 
                WHERE d.user_id = ?
            """, (user_id,))
            items = await cur.fetchall()

            if not items:
                await call.answer("Букет пуст! Добавьте цветы.", show_alert=True)
                return

            # 2. Считаем и формируем описание
            total_price = 0
            desc_parts = []
            for name, price, qty in items:
                total_price += price * qty
                desc_parts.append(f"{name} ({qty})")

            pack_price = 0
            pack_text = "Без упаковки"
            if data == "pack_yes":
                pack_price = 15
                total_price += pack_price
                pack_text = "В упаковке"

            final_desc = f"Состав: {', '.join(desc_parts)}. {pack_text}."

            # --- ИСПРАВЛЕНИЕ ОШИБКИ UNIQUE ---
            # Добавляем случайное число, чтобы имя всегда было уникальным
            rand_id = random.randint(10000, 99999)
            final_name = f"Авторский букет №{rand_id}"

            # 3. Создаем временный продукт
            try:
                await db.execute(
                    "INSERT INTO products (name, price, description, type) VALUES (?, ?, ?, ?)",
                    (final_name, total_price, final_desc, "created_bouquet")
                )
            except Exception as e:
                # На случай, если вдруг рандом совпадет (шанс мизерный, но перестрахуемся)
                final_name = f"Авторский букет №{rand_id+1}"
                await db.execute(
                    "INSERT INTO products (name, price, description, type) VALUES (?, ?, ?, ?)",
                    (final_name, total_price, final_desc, "created_bouquet")
                )

            # Получаем ID только что созданного букета
            cur = await db.execute("SELECT last_insert_rowid()")
            new_product_id_row = await cur.fetchone()
            new_product_id = new_product_id_row[0]

            # 4. Добавляем новый букет в корзину
            await db.execute(
                "INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1)",
                (user_id, new_product_id)
            )

            # 5. Очищаем черновик
            await db.execute("DELETE FROM bouquet_draft WHERE user_id = ?", (user_id,))

            # --- ИСПРАВЛЕНИЕ ПРОПАДАНИЯ БУКЕТА ---
            # Если мы редактировали старый букет, удаляем ЕГО только сейчас, когда новый успешно создан
            if user_id in user_states and 'editing_pid' in user_states[user_id]:
                old_pid = user_states[user_id]['editing_pid']
                await db.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, old_pid))
                # Можно (опционально) удалить и сам старый продукт из таблицы products, чтобы не мусорить
                # await db.execute("DELETE FROM products WHERE id = ?", (old_pid,))
                del user_states[user_id]['editing_pid'] # Очищаем состояние

            await db.commit()

        # Сообщение об успехе
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧺 Перейти в корзину", callback_data="view_cart")],
            [InlineKeyboardButton(text="🌸 Собрать ещё один", callback_data="create_bouquet")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")]
        ])
        await call.message.edit_text(
            f"🎉 <b>Готово!</b>\n\nВаш «{final_name}» добавлен в корзину.\n\n"
            f"📝 {final_desc}\n💰 <b>Цена: {total_price} ₽</b>",
            reply_markup=kb, parse_mode="HTML"
        )
        return

    # Просмотр корзины
    if data == "view_cart":
        items = await get_cart(user_id)
        if not items:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]])
            await call.message.edit_text("🧺 Ваша корзина пуста — время добавить немного цветов!", reply_markup=kb)
            await call.answer()
            return

        # Формируем текст корзины
        lines = []
        total = 0
        for pid, name, price, qty, desc, p_type in items:
            summ = price * qty
            total += summ
            # Основная строка
            item_text = f"🔹 <b>{name}</b>\n     {price} ₽ × {qty} шт. = {summ} ₽"

            # Если это авторский букет, добавляем состав (он лежит в description)
            if p_type == "created_bouquet" and desc:
                # Убираем "Состав: " для красоты, если оно там есть, и делаем курсивом
                clean_desc = desc.replace("Состав: ", "").strip()
                item_text += f"\n     <i>└ {clean_desc}</i>"

            lines.append(item_text)

        text = "<b>🧺 Ваша корзина:</b>\n\n" + "\n\n".join(
            lines) + f"\n\n💰 Итого к оплате: <b>{total} ₽</b>\n\nМы приготовим всё красиво и аккуратно — осталось оформить."
        await call.message.edit_text(text, reply_markup=cart_kb(items), parse_mode="HTML")
        await call.answer()
        return

    # Логика кнопки "Изменить букет"
    if data.startswith("edit_bouquet_"):
        try:
            pid_to_edit = int(data.split("_")[2])
        except:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT description FROM products WHERE id = ?", (pid_to_edit,))
            row = await cur.fetchone()
            if not row:
                await call.answer("Товар не найден", show_alert=True)
                return

            description = row[0]

            # Очищаем черновик
            await db.execute("DELETE FROM bouquet_draft WHERE user_id = ?", (user_id,))

            # Парсим состав
            try:
                composition_part = description.split("Состав: ")[-1].split(".")[0]
                items_str = [s.strip() for s in composition_part.split(",")]

                for item_str in items_str:
                    if "(" in item_str and ")" in item_str:
                        flower_name = item_str.split(" (")[0]
                        qty_str = item_str.split(" (")[1].replace(")", "")

                        if qty_str.isdigit():
                            qty = int(qty_str)
                            cur = await db.execute("SELECT id FROM products WHERE name = ?", (flower_name,))
                            prod_row = await cur.fetchone()
                            if prod_row:
                                real_prod_id = prod_row[0]
                                await db.execute(
                                    "INSERT INTO bouquet_draft (user_id, product_id, quantity) VALUES (?, ?, ?)",
                                    (user_id, real_prod_id, qty))
            except Exception:
                pass

            await db.commit()

        # --- ИСПРАВЛЕНИЕ ---
        # Мы НЕ удаляем старый букет из корзины здесь.
        # Мы просто запоминаем ID редактируемого букета в user_states.
        # Если пользователь нажмет "Назад", букет останется в корзине.
        # Если нажмет "Упаковать", мы удалим старый ID в блоке pack_yes/no.
        if user_id not in user_states:
            user_states[user_id] = {}
        user_states[user_id]['editing_pid'] = pid_to_edit

        # Переходим в меню создания
        await show_creation_menu(call.message, user_id)
        await call.answer()
        return

    # Очистить корзину
    if data == "clear_cart":
        await clear_cart(user_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]])
        await call.message.edit_text("🧹 Корзина очищена — можно начать заново.", reply_markup=kb)
        await call.answer(text="Корзина очищена")
        return

    if data.startswith("remove_"):
        try:
            pid = int(data.split("_", 1)[1])
        except Exception:
            await call.answer("Неверные данные", show_alert=True)
            return

        await remove_one_from_cart(user_id, pid)

        # Обновляем отображение корзины
        items = await get_cart(user_id)
        if not items:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]])
            await call.message.edit_text("🧺 Ваша корзина пуста.", reply_markup=kb)
            await call.answer()
            return

        # ИСПРАВЛЕНИЕ ЗДЕСЬ: распаковываем 6 переменных (или используем *_)
        lines = []
        total = 0
        for pid, name, price, qty, desc, p_type in items:
            lines.append(f"{name} — {price} ₽ × {qty} = {price * qty} ₽")
            total += price * qty

        text = "<b>🧺 Ваша корзина:</b>\n\n" + "\n".join(lines) + f"\n\nИтого: <b>{total} ₽</b>"
        await call.message.edit_text(text, reply_markup=cart_kb(items), parse_mode="HTML")
        await call.answer(text="Удалено")
        return

        # Добавить готовый букет (нажатие на кнопку в меню)
    if data.startswith("plus_bouquet_"):
        try:
            pid = int(data.replace("plus_bouquet_", ""))
        except Exception:
            return

        await add_to_cart(user_id, pid, 1)
        await call.answer("✅ Добавлено в корзину!", show_alert=False)
        return

    if data == 'addr_confirm_yes':
        # Данные уже сохранены в temp_address, переходим ко времени
        await state.set_state(OrderState.waiting_for_time)

        await call.message.edit_text(
            "✅ Адрес сохранён!\n\n"
            "Теперь напишите, к какому <b>времени и дате</b> нужно доставить букет?\n"
            "<i>(Например: Завтра к 18:00)</i>",
            parse_mode="HTML"
        )
        await call.answer()
        return
    # По умолчанию — acknowledge
    await call.answer()



# ==========================================
#FSM
# ==========================================

# --- ШАГ 1: ПОЛУЧАЕМ АДРЕС ---
@dp.message(OrderState.waiting_for_address)
async def process_address_input(message: Message, state: FSMContext):
    address = message.text  # То, что написал пользователь
    await state.update_data(temp_address=address)
    await state.set_state(OrderState.waiting_for_address)

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Да, всё верно", callback_data="addr_confirm_yes")]])
    await message.answer(
        f"Проверим адрес:\n\n<b>{address}</b>\n\nВсё верно?\n<b>Если нет просто отправьте тот адресс который нужно</b>",
        reply_markup=kb, parse_mode="HTML")
    return

@dp.message(OrderState.waiting_for_time)
async def process_time_input(message: Message, state: FSMContext):
    delivery_time = message.text
    await state.update_data(delivery_time=delivery_time)
    await state.set_state(OrderState.waiting_for_payment_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Криптовалюта (USDT)", callback_data="pay_crypto"),
         InlineKeyboardButton(text="🟠 Portmone (Карта)", callback_data="pay_portmone")], # <--- Добавили
        [InlineKeyboardButton(text="💵 На месте (при получении)", callback_data="pay_onsite")]
    ])
    await message.answer(
        f"✅ Время доставки: <b>{delivery_time}</b>\n\n"
        "Остался последний шаг. Выберите удобный способ оплаты: 👇",
        reply_markup=kb, parse_mode="HTML")
    return

@dp.message()
async def fallback_message(message: Message):
    user_id = message.from_user.id
    if user_id in user_states and 'waiting_for_qty' in user_states[user_id]:
        pid = user_states[user_id]['waiting_for_qty']
        name = user_states[user_id]['product_name']
        try:
            qty = int(message.text.strip())
            if qty <= 0:
                await message.answer("Количество должно быть положительным целым числом. 🌸")
                return
            await add_to_cart(user_id, pid, qty)
            await message.answer(f"Добавлено {qty} шт. «{name}» в корзину! 🌷 Вы можете продолжить выбор или перейти в корзину.")
        except ValueError:
            await message.answer("Пожалуйста, введите целое число. 🌿")
            return
        finally:
            user_states.pop(user_id, None)
    else:
        await message.answer("Привет! Отправь /start чтобы открыть каталог 🌿\n\nЕсли нужно быстро связаться с нами — напиши здесь сообщение, и мы ответим как можно скорее. 💌")

# --------- Запуск ---------
async def main():
    await init_db()
    print(f"{datetime.now().isoformat()} — Бот запускается")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
