import os
import asyncio
import sqlite3
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Any

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

"""
StarGifty — клон @AutoOneRobot под Telegram Stars (XTR)
Polling-версия (для локального теста)

• Источник NFT: «маркет в Telegram» (интеграция через клиент-адаптер ниже).
• Оплата: только звёздами (XTR). Для Stars provider_token пустой, валюта "XTR".
• Режимы: ручная покупка и автоснайпер (подписка на коллекцию/цене).
• Дарение другу: @username или TON-адрес + открытка (бренд/кастом).

⚠️ ВАЖНО: методы TelegramMarketClient — заглушки. Туда подключается реальный
API/бот маркета в ТГ. Логика покупок/трансфера и статусов уже разнесена.
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Please set BOT_TOKEN in .env or environment")

DB_PATH = os.getenv("DB_PATH", "stargifty.db")
STARS_CURRENCY = "XTR"
BOT_BRAND = "StarGifty"

# --- Domain models ---
@dataclass
class MarketItem:
    item_id: str
    collection: str
    title: str
    price_stars: int
    img: Optional[str] = None


@dataclass
class GiftOrder:
    id: Optional[int]
    user_id: int
    item_id: str
    collection: str
    price_stars: int
    recipient: str  # @username или TON-адрес
    card_msg: str
    status: str  # created|paid|bought|sent|failed
    tx_id: Optional[str] = None


# --- Persistence ---
class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self):
        c = self.conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance_stars INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                collection TEXT NOT NULL,
                max_price_stars INTEGER NOT NULL,
                recipient TEXT NOT NULL,
                card_msg TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                collection TEXT NOT NULL,
                price_stars INTEGER NOT NULL,
                recipient TEXT NOT NULL,
                card_msg TEXT NOT NULL,
                status TEXT NOT NULL,
                tx_id TEXT
            )
            """
        )
        self.conn.commit()

    # Users / balances
    def ensure_user(self, user_id: int):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO users(user_id, balance_stars) VALUES(?, 0)",
                (user_id,),
            )

    def balance(self, user_id: int) -> int:
        self.ensure_user(user_id)
        row = self.conn.execute("SELECT balance_stars FROM users WHERE user_id=?", (user_id,)).fetchone()
        return int(row[0]) if row else 0

    def add_balance(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        with self.conn:
            self.conn.execute(
                "UPDATE users SET balance_stars = balance_stars + ? WHERE user_id=?",
                (amount, user_id),
            )

    def sub_balance(self, user_id: int, amount: int) -> bool:
        self.ensure_user(user_id)
        if self.balance(user_id) < amount:
            return False
        with self.conn:
            self.conn.execute(
                "UPDATE users SET balance_stars = balance_stars - ? WHERE user_id=?",
                (amount, user_id),
            )
        return True

    # Subscriptions
    def add_sub(self, user_id: int, collection: str, max_price: int, recipient: str, card_msg: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO subs(user_id, collection, max_price_stars, recipient, card_msg, active) VALUES(?,?,?,?,?,1)",
                (user_id, collection, max_price, recipient, card_msg),
            )
            return cur.lastrowid

    def list_subs(self, user_id: int):
        rows = self.conn.execute(
            "SELECT * FROM subs WHERE user_id=? ORDER BY id DESC", (user_id,)
        ).fetchall()
        return rows

    def toggle_sub(self, user_id: int, sub_id: int, active: bool):
        with self.conn:
            self.conn.execute(
                "UPDATE subs SET active=? WHERE id=? AND user_id=?",
                (1 if active else 0, sub_id, user_id),
            )

    def active_subs(self):
        rows = self.conn.execute("SELECT * FROM subs WHERE active=1").fetchall()
        return rows

    # Orders
    def create_order(self, o: GiftOrder) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO orders(user_id,item_id,collection,price_stars,recipient,card_msg,status,tx_id)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (o.user_id, o.item_id, o.collection, o.price_stars, o.recipient, o.card_msg, o.status, o.tx_id),
            )
            return cur.lastrowid

    def update_order(self, order_id: int, **fields):
        keys = ",".join(f"{k}=?" for k in fields.keys())
        vals = list(fields.values()) + [order_id]
        with self.conn:
            self.conn.execute(f"UPDATE orders SET {keys} WHERE id=?", vals)


# --- Telegram Market client stub ---
class TelegramMarketClient:
    """Заглушка клиента маркета в Telegram.
    Реализуйте реальные вызовы/бот-команды/HTTP здесь.
    """

    def __init__(self):
        pass

    async def search_current_listings(self, collection: str, limit: int = 10) -> List[MarketItem]:
        # TODO: заменить на реальные данные с маркета в ТГ
        demo = [
            MarketItem(item_id=f"{collection}-#{i}", collection=collection, title=f"{collection.upper()} NFT #{i}", price_stars=100 + 25 * i)
            for i in range(1, 6)
        ]
        return demo[:limit]

    async def search_new_listings(self, collection: str, max_price_stars: int) -> List[MarketItem]:
        # TODO: подписка на новые листинги/стрим. Возвращать только подходящие по цене
        items = await self.search_current_listings(collection, limit=3)
        return [x for x in items if x.price_stars <= max_price_stars]

    async def buy_item(self, item: MarketItem):
        # TODO: реальная покупка на маркете. Вернуть (ok, deal_id)
        await asyncio.sleep(0.2)
        return True, f"deal-{item.item_id}"

    async def transfer_nft(self, item: MarketItem, recipient: str, card_msg: str):
        # TODO: реальный трансфер в TON получателю (@username → resolve → адрес)
        await asyncio.sleep(0.3)
        return True, f"tx-{item.item_id}"


# --- Utilities ---
def kb_builder(pairs: List[Tuple[str, str]], cols: int = 1):
    kb = InlineKeyboardBuilder()
    for text, cb in pairs:
        kb.button(text=text, callback_data=cb)
    kb.adjust(cols)
    return kb.as_markup()


def stars_prices(amount_stars: int) -> List[LabeledPrice]:
    return [LabeledPrice(label="Оплата в звёздах", amount=amount_stars)]


# --- Bot setup ---
router = Router()
db = DB(DB_PATH)
market = TelegramMarketClient()


# --- FSMs ---
class ManualBuy(StatesGroup):
    choose_collection = State()
    choose_item = State()
    set_recipient = State()
    set_card = State()


class SubForm(StatesGroup):
    choose_collection = State()
    set_price = State()
    set_recipient = State()
    set_card = State()


COLLECTION_CHOICES = [
    ("🎨 gift-cards", "gift-cards"),
    ("🎬 experiences", "experiences"),
    ("💎 collectibles", "collectibles"),
]

DEFAULT_CARD = f"Поздравляю! Подарок из {BOT_BRAND} ✨"


# --- Start / Menu ---
@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext):
    await state.clear()
    db.ensure_user(message.from_user.id)
    await message.answer(
        f"Привет! Я <b>{BOT_BRAND}</b> — помогаю покупать и дарить NFT-подарки за ⭐️ Telegram Stars.\\n\\n"
        "Доступно: ручная покупка и автоснайпер по коллекции/цене.",
        reply_markup=kb_builder([
            ("🛍 Ручная покупка", "manual:start"),
            ("🎯 Автоснайпер", "sub:start"),
            ("💰 Пополнить баланс", "wallet:deposit"),
            ("💼 Баланс/подписки", "account:open"),
            ("ℹ️ Справка", "help:open"),
        ], cols=1),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "help:open")
async def on_help(call: CallbackQuery):
    await call.message.edit_text(
        "<b>Как это работает</b>\\n\\n"
        "• Ручная покупка: выбираешь коллекцию → лот → оплачиваешь ⭐️ → мы покупаем и передаём NFT получателю.\\n"
        "• Автоснайпер: оформляешь подписку (коллекция + максимальная цена), пополняешь баланс ⭐️; бот ловит подходящие листинги и покупает автоматически.\\n\\n"
        "Открытка: по умолчанию \"" + DEFAULT_CARD + "\", можно написать свою.",
        reply_markup=kb_builder([("⬅️ В меню", "menu:main")]),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "menu:main")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await on_start(call.message, state)


# --- Wallet (Stars internal balance for auto-buy) ---
@router.callback_query(F.data == "wallet:deposit")
async def wallet_deposit(call: CallbackQuery):
    kb = InlineKeyboardBuilder()
    for amount in [100, 300, 500, 1000, 2500]:
        kb.button(text=f"Пополнить на {amount}⭐️", callback_data=f"deposit:{amount}")
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(1)
    await call.message.edit_text("Выбери сумму пополнения:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("deposit:"))
async def deposit_invoice(call: CallbackQuery):
    amount = int(call.data.split(":", 1)[1])
    await call.bot.send_invoice(
        chat_id=call.message.chat.id,
        title=f"Пополнение {BOT_BRAND}",
        description=f"Зачисление на баланс для автопокупок. Сумма: {amount}⭐️",
        payload=f"deposit:{amount}",
        provider_token="",
        currency=STARS_CURRENCY,
        prices=stars_prices(amount),
    )


@router.callback_query(F.data == "account:open")
async def account_open(call: CallbackQuery):
    bal = db.balance(call.from_user.id)
    subs = db.list_subs(call.from_user.id)
    lines = [f"<b>Баланс:</b> {bal}⭐️", "", "<b>Подписки:</b>"]
    if not subs:
        lines.append("— нет активных подписок")
    else:
        for s in subs:
            status = "✅" if s["active"] else "⏸"
            lines.append(f"{status} #{s['id']} {s['collection']} ≤ {s['max_price_stars']}⭐️ → {s['recipient']}")
    kb = InlineKeyboardBuilder()
    kb.button(text="🎯 Создать подписку", callback_data="sub:start")
    kb.button(text="💰 Пополнить", callback_data="wallet:deposit")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    await call.message.edit_text("\\n".join(lines), reply_markup=kb.as_markup(), parse_mode="HTML")


# --- Manual Buy flow ---
@router.callback_query(F.data == "manual:start")
async def manual_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(ManualBuy.choose_collection)
    await call.message.edit_text(
        "Выбери коллекцию:",
        reply_markup=kb_builder([(t, f"manual:col:{val}") for t, val in COLLECTION_CHOICES] + [("⬅️ Назад", "menu:main")]),
    )


@router.callback_query(F.data.startswith("manual:col:"))
async def manual_choose_collection(call: CallbackQuery, state: FSMContext):
    col = call.data.split(":", 2)[2]
    await state.update_data(collection=col)
    items = await market.search_current_listings(col)

    if not items:
        await call.answer("Нет доступных лотов.", show_alert=True)
        return

    ids = ",".join([i.item_id for i in items])
    await state.set_state(ManualBuy.choose_item)
    await call.message.edit_text(
        f"Найдено {len(items)} лотов. Открой карточки:",
        reply_markup=kb_builder([("Показать (" + str(len(items)) + ")", f"manual:list:0:{ids}") , ("⬅️ Назад", "manual:start")])
    )


@router.callback_query(F.data.startswith("manual:list:"))
async def manual_show_item(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")  # manual:list:idx:ids
    idx = int(parts[2])
    ids = parts[3].split(",")
    if idx >= len(ids):
        idx = 0
    item_id = ids[idx]
    data = await state.get_data()
    collection = data.get("collection")
    items = await market.search_current_listings(collection)
    item = next((x for x in items if x.item_id == item_id), None)
    if not item:
        await call.answer("Лот не найден", show_alert=True)
        return

    text = (
        f"<b>{item.title}</b>\\nКоллекция: <code>{item.collection}</code>\\nЦена: <b>{item.price_stars}⭐️</b>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Выбрать получателя", callback_data=f"manual:pick:{item.item_id}:{item.price_stars}")
    if len(ids) > 1:
        kb.button(text="◀️ Пред.", callback_data=f"manual:list:{(idx-1)%len(ids)}:{','.join(ids)}")
        kb.button(text="След. ▶️", callback_data=f"manual:list:{(idx+1)%len(ids)}:{','.join(ids)}")
    kb.button(text="⬅️ Назад", callback_data="manual:start")
    kb.adjust(2)

    await call.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("manual:pick:"))
async def manual_pick_recipient(call: CallbackQuery, state: FSMContext):
    _, _, item_id, price = call.data.split(":")
    await state.update_data(item_id=item_id, price=int(price))
    await state.set_state(ManualBuy.set_recipient)
    await call.message.edit_text(
        "Введи @username получателя или TON-адрес в ответ на это сообщение",
    )


@router.message(ManualBuy.set_recipient)
async def manual_set_recipient(message: Message, state: FSMContext):
    recip = message.text.strip()
    await state.update_data(recipient=recip)
    await state.set_state(ManualBuy.set_card)
    await message.answer(
        "Текст открытки? Отправь текст или напиши \"бренд\" для варианта по умолчанию.",
    )


@router.message(ManualBuy.set_card)
async def manual_set_card(message: Message, state: FSMContext):
    card = message.text.strip()
    if card.lower() == "бренд":
        card = DEFAULT_CARD
    data = await state.get_data()
    item_id = data["item_id"]
    price = data["price"]
    await state.clear()

    # Выставляем инвойс на точную стоимость лота.
    await message.answer(
        f"Оформляем покупку <code>{item_id}</code> за <b>{price}⭐️</b>. Оплати счёт:", parse_mode="HTML"
    )
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title=f"Покупка NFT — {BOT_BRAND}",
        description=f"Лот {item_id}. После оплаты купим на маркете и передадим получателю.",
        payload=f"manual:{item_id}:{price}:{message.from_user.id}:{card}:{data['recipient']}",
        provider_token="",
        currency=STARS_CURRENCY,
        prices=stars_prices(price),
    )


# --- Subscriptions (Auto Sniper) ---
@router.callback_query(F.data == "sub:start")
async def sub_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(SubForm.choose_collection)
    await call.message.edit_text(
        "Выбери коллекцию для автопокупок:",
        reply_markup=kb_builder([(t, f"sub:col:{val}") for t, val in COLLECTION_CHOICES] + [("⬅️ Назад", "menu:main")])
    )


@router.callback_query(F.data.startswith("sub:col:"))
async def sub_col(call: CallbackQuery, state: FSMContext):
    col = call.data.split(":", 2)[2]
    await state.update_data(collection=col)
    await state.set_state(SubForm.set_price)
    kb = InlineKeyboardBuilder()
    for p in [100, 200, 300, 500, 800, 1200]:
        kb.button(text=f"≤ {p}⭐️", callback_data=f"sub:price:{p}")
    kb.button(text="⬅️ Назад", callback_data="sub:start")
    kb.adjust(3)
    await call.message.edit_text("Лимит цены (звёзды):", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("sub:price:"))
async def sub_price(call: CallbackQuery, state: FSMContext):
    p = int(call.data.split(":", 2)[2])
    await state.update_data(max_price=p)
    await state.set_state(SubForm.set_recipient)
    await call.message.edit_text("Кому дарим по умолчанию? Введи @username или TON-адрес.")


@router.message(SubForm.set_recipient)
async def sub_recipient(message: Message, state: FSMContext):
    recip = message.text.strip()
    await state.update_data(recipient=recip)
    await state.set_state(SubForm.set_card)
    await message.answer("Текст открытки по умолчанию (или напиши \"бренд\"): ")


@router.message(SubForm.set_card)
async def sub_card(message: Message, state: FSMContext):
    card = message.text.strip()
    if card.lower() == "бренд":
        card = DEFAULT_CARD
    data = await state.get_data()
    sub_id = db.add_sub(
        user_id=message.from_user.id,
        collection=data["collection"],
        max_price=data["max_price"],
        recipient=data["recipient"],
        card_msg=card,
    )
    await state.clear()
    await message.answer(
        f"✅ Подписка #{sub_id} создана: {data['collection']} ≤ {data['max_price']}⭐️ → {data['recipient']}\\n"
        "Пополните баланс звёздами для автопокупок: /deposit",
    )


# --- Payments ---
@router.pre_checkout_query()
async def pre_checkout(pre: PreCheckoutQuery):
    await pre.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    sp = message.successful_payment
    payload = sp.invoice_payload

    if payload.startswith("deposit:"):
        amount = int(payload.split(":", 1)[1])
        db.add_balance(message.from_user.id, amount)
        await message.answer(f"💰 Зачислено: {amount}⭐️. Баланс: {db.balance(message.from_user.id)}⭐️")
        return

    if payload.startswith("manual:" ):
        # payload: manual:item_id:price:user_id:card:recipient
        _, item_id, price, uid, card_msg, recipient = payload.split(":", 5)
        price = int(price)
        user_id = int(uid)

        # Безопасность: убедимся, что оплачивал тот же пользователь
        if user_id != message.from_user.id:
            await message.answer("Оплата получена не от владельца заявки.")
            return

        # Пытаемся купить и передать NFT
        collection = item_id.split("-#")[0]
        itm = MarketItem(item_id=item_id, collection=collection, title=item_id, price_stars=price)
        order_id = db.create_order(GiftOrder(
            id=None,
            user_id=user_id,
            item_id=item_id,
            collection=collection,
            price_stars=price,
            recipient=recipient,
            card_msg=card_msg,
            status="paid",
        ))
        await message.answer("Оплата получена. Покупаю на маркете…")
        ok, deal_id = await market.buy_item(itm)
        if not ok:
            db.add_balance(user_id, price)
            db.update_order(order_id, status="failed")
            await message.answer(
                "❌ Не удалось купить лот (возможно, уже выкупили). Сумма зачислена на ваш баланс."
            )
            return

        ok2, tx = await market.transfer_nft(itm, recipient, card_msg)
        if ok2:
            db.update_order(order_id, status="sent", tx_id=tx)
            await message.answer(
                "✅ Готово! NFT передан получателю.\\n"
                f"Транзакция: <code>{tx}</code>", parse_mode="HTML"
            )
        else:
            db.update_order(order_id, status="bought")
            await message.answer("⚠️ Купили, но не удалось передать автоматически. Попробуем ещё раз позже.")
        return


# --- Cart/Balance commands (shortcuts) ---
@router.message(Command("balance"))
async def cmd_balance(message: Message):
    await message.answer(f"Ваш баланс: {db.balance(message.from_user.id)}⭐️")


@router.message(Command("deposit"))
async def cmd_deposit(message: Message):
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title=f"Пополнение {BOT_BRAND}",
        description="Зачисление на баланс для автопокупок.",
        payload="deposit:300",
        provider_token="",
        currency=STARS_CURRENCY,
        prices=stars_prices(300),
    )


# --- Background sniper worker ---
SCAN_INTERVAL_SEC = 8

async def sniper_worker(dp: Dispatcher):
    await asyncio.sleep(2)
    bot = dp.bot
    while True:
        try:
            for s in db.active_subs():
                user_id = int(s["user_id"])
                collection = s["collection"]
                max_price = int(s["max_price_stars"])
                recipient = s["recipient"]
                card_msg = s["card_msg"]

                items = await market.search_new_listings(collection, max_price)
                if not items:
                    continue

                bal = db.balance(user_id)
                for item in items:
                    if item.price_stars > max_price:
                        continue
                    if bal < item.price_stars:
                        try:
                            await bot.send_message(user_id, f"Недостаточно ⭐️ для автопокупки {item.title} ({item.price_stars}⭐️). Пополните баланс.")
                        except Exception:
                            pass
                        continue

                    if not db.sub_balance(user_id, item.price_stars):
                        continue
                    ok, deal_id = await market.buy_item(item)
                    if not ok:
                        db.add_balance(user_id, item.price_stars)
                        continue

                    ok2, tx = await market.transfer_nft(item, recipient, card_msg)
                    order_id = db.create_order(GiftOrder(
                        id=None,
                        user_id=user_id,
                        item_id=item.item_id,
                        collection=item.collection,
                        price_stars=item.price_stars,
                        recipient=recipient,
                        card_msg=card_msg,
                        status="sent" if ok2 else "bought",
                        tx_id=tx if ok2 else None,
                    ))

                    try:
                        if ok2:
                            await bot.send_message(
                                user_id,
                                f"🎯 Автопокупка: {item.title} за {item.price_stars}⭐️\\nПередан: {recipient}. Tx: {tx}"
                            )
                        else:
                            await bot.send_message(
                                user_id,
                                f"Купили {item.title}, но не смогли передать автоматически. Заказ #{order_id}. Попробуем повтор позже."
                            )
                    except Exception:
                        pass
        except Exception as e:
            print("Sniper error:", e)
        await asyncio.sleep(SCAN_INTERVAL_SEC)


# --- App bootstrap ---
async def main():
    bot = Bot(BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    asyncio.create_task(sniper_worker(dp))

    print(f"{BOT_BRAND} bot is running… (polling)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped")
