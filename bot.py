import os
import json
import re
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from groq import Groq
 
# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN_HERE")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "YOUR_GROQ_KEY")
DATA_FILE = "data.json"
 
CATEGORIES = {
    "еда": "🍕",
    "стройка": "🏗️",
    "бытовое": "🧹",
    "коммуналка": "💡",
    "другое": "📦",
}
 
SMALL_LIMIT = 100
LARGE_LIMIT = 1000
 
# ─── DATA LAYER ───────────────────────────────────────────────────────────────
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"expenses": [], "shopping_list": [], "last_monthly_report": None}
 
def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
 
# ─── AI PARSING ───────────────────────────────────────────────────────────────
def parse_expenses_with_ai(text: str, username: str) -> list[dict]:
    client = Groq(api_key=GROQ_API_KEY)
 
    prompt = f"""Пользователь написал о своих тратах: "{text}"
 
Извлеки все товары и их стоимость. Верни ТОЛЬКО JSON массив, без пояснений, без markdown.
Формат каждого элемента:
{{
  "name": "название товара",
  "total": итоговая сумма в рублях (число),
  "quantity": количество (число или null),
  "price_per_unit": цена за штуку (число или null),
  "category": одна из: "еда", "стройка", "бытовое", "коммуналка", "другое"
}}
 
Примеры:
- "цемент 3 мешка по 600" → name="Цемент", total=1800, quantity=3, price_per_unit=600, category="стройка"
- "хлеб 30 рублей" → name="Хлеб", total=30, quantity=null, price_per_unit=null, category="еда"
- "огурцы 600" → name="Огурцы", total=600, quantity=null, price_per_unit=null, category="еда"
 
Верни только JSON массив, никакого другого текста."""
 
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        temperature=0.1,
    )
 
    response_text = response.choices[0].message.content.strip()
    response_text = re.sub(r"```json\s*|\s*```", "", response_text).strip()
 
    items = json.loads(response_text)
 
    today = date.today().isoformat()
    result = []
    for i, item in enumerate(items):
        result.append({
            "id": datetime.now().timestamp() + i * 0.001,
            "user": username,
            "name": item["name"],
            "total": float(item["total"]),
            "quantity": item.get("quantity"),
            "price_per_unit": item.get("price_per_unit"),
            "category": item.get("category", "другое"),
            "date": today,
            "size": classify_size(float(item["total"])),
        })
    return result
 
def classify_size(amount: float) -> str:
    if amount < SMALL_LIMIT:
        return "мелкая"
    elif amount < LARGE_LIMIT:
        return "средняя"
    else:
        return "крупная"
 
# ─── FORMATTERS ───────────────────────────────────────────────────────────────
def format_expense_line(e: dict) -> str:
    cat_icon = CATEGORIES.get(e["category"], "📦")
    size_icon = {"мелкая": "🟢", "средняя": "🟡", "крупная": "🔴"}.get(e["size"], "⚪")
    detail = ""
    if e.get("quantity") and e.get("price_per_unit"):
        detail = f" ({e['quantity']} × {e['price_per_unit']:.0f}р)"
    return f"{size_icon}{cat_icon} {e['name']}{detail} — *{e['total']:.0f}р*"
 
def format_all_table(expenses: list[dict]) -> str:
    if not expenses:
        return "📭 Трат пока нет."
    lines = ["📋 *Все траты:*\n"]
    for e in sorted(expenses, key=lambda x: x["date"], reverse=True):
        lines.append(f"👤 {e['user']} | {e['date']}")
        lines.append(format_expense_line(e))
        lines.append("")
    return "\n".join(lines)
 
def format_user_table(expenses: list[dict], username: str) -> str:
    user_exp = [e for e in expenses if e["user"] == username]
    if not user_exp:
        return f"📭 У {username} нет трат."
 
    total = sum(e["total"] for e in user_exp)
    small = sum(e["total"] for e in user_exp if e["size"] == "мелкая")
    medium = sum(e["total"] for e in user_exp if e["size"] == "средняя")
    large = sum(e["total"] for e in user_exp if e["size"] == "крупная")
 
    lines = [f"👤 *Траты {username}:*\n"]
    for e in sorted(user_exp, key=lambda x: x["date"], reverse=True):
        lines.append(f"{e['date']} | {format_expense_line(e)}")
 
    lines.append(f"\n💰 *Итого: {total:.0f}р*")
    lines.append(f"🟢 Мелкие: {small:.0f}р  🟡 Средние: {medium:.0f}р  🔴 Крупные: {large:.0f}р")
    return "\n".join(lines)
 
def format_balance(expenses: list[dict]) -> str:
    if not expenses:
        return "📭 Трат пока нет."
 
    users = {}
    for e in expenses:
        users.setdefault(e["user"], 0)
        users[e["user"]] += e["total"]
 
    total = sum(users.values())
    n = len(users)
    share = total / n if n else 0
 
    lines = ["💰 *Текущий баланс:*\n"]
    for user, spent in sorted(users.items(), key=lambda x: -x[1]):
        diff = spent - share
        sign = "+" if diff >= 0 else ""
        lines.append(f"👤 {user}: потратил *{spent:.0f}р* ({sign}{diff:.0f}р от доли)")
 
    lines.append(f"\n📊 Общая сумма: *{total:.0f}р*")
    lines.append(f"📐 Доля на человека: *{share:.0f}р*")
 
    # Кто кому должен
    sorted_users = sorted(users.items(), key=lambda x: x[1])
    creditors = [(u, s - share) for u, s in sorted_users if s > share]
    debtors = [(u, share - s) for u, s in sorted_users if s < share]
 
    if creditors and debtors:
        lines.append("\n📨 *Кто кому платит:*")
        d_list = list(debtors)
        c_list = list(creditors)
        i, j = 0, 0
        while i < len(d_list) and j < len(c_list):
            debtor, debt = d_list[i]
            creditor, credit = c_list[j]
            amount = min(debt, credit)
            lines.append(f"  {debtor} → {creditor}: *{amount:.0f}р*")
            d_list[i] = (debtor, debt - amount)
            c_list[j] = (creditor, credit - amount)
            if d_list[i][1] < 1:
                i += 1
            if c_list[j][1] < 1:
                j += 1
 
    return "\n".join(lines)
 
def format_monthly_report(expenses: list[dict], month: str) -> str:
    month_exp = [e for e in expenses if e["date"].startswith(month)]
    if not month_exp:
        return f"📭 Нет трат за {month}."
 
    lines = [f"📅 *Итог за {month}:*\n"]
    lines.append(format_balance(month_exp))
 
    lines.append("\n📂 *По категориям:*")
    cats = {}
    for e in month_exp:
        cats.setdefault(e["category"], 0)
        cats[e["category"]] += e["total"]
    for cat, amount in sorted(cats.items(), key=lambda x: -x[1]):
        icon = CATEGORIES.get(cat, "📦")
        lines.append(f"  {icon} {cat.capitalize()}: *{amount:.0f}р*")
 
    return "\n".join(lines)
 
def format_shopping_list(shopping_list: list[dict]) -> str:
    if not shopping_list:
        return "🛒 Список покупок пуст."
    lines = ["🛒 *Список покупок:*\n"]
    for i, item in enumerate(shopping_list):
        done = "✅" if item.get("done") else "⬜"
        lines.append(f"{done} {i+1}. {item['name']} (добавил {item['added_by']})")
    return "\n".join(lines)
 
# ─── HANDLERS ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Привет! Я бот для общих трат.*\n\n"
        "Просто напиши что купил, например:\n"
        "_купил цемент 3 мешка по 600, хлеб 30 и огурцы 200_\n\n"
        "📌 *Команды:*\n"
        "/all — все траты\n"
        "/mine — мои траты\n"
        "/balance — кто сколько должен\n"
        "/summary — итог текущего месяца\n"
        "/search [запрос] — поиск по тратам\n"
        "/list — список покупок\n"
        "/buy [товар] — добавить в список\n"
        "/bought [номер] — отметить куплено\n"
        "/undo — удалить последнюю трату\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
 
async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    await update.message.reply_text(format_all_table(data["expenses"]), parse_mode="Markdown")
 
async def cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    username = update.effective_user.first_name or update.effective_user.username
    await update.message.reply_text(format_user_table(data["expenses"], username), parse_mode="Markdown")
 
async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    await update.message.reply_text(format_balance(data["expenses"]), parse_mode="Markdown")
 
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    month = date.today().strftime("%Y-%m")
    await update.message.reply_text(format_monthly_report(data["expenses"], month), parse_mode="Markdown")
 
async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).lower().strip()
    if not query:
        await update.message.reply_text("🔍 Использование: /поиск [запрос]\nПример: /поиск цемент")
        return
 
    data = load_data()
    results = [
        e for e in data["expenses"]
        if query in e["name"].lower()
        or query in e["category"].lower()
        or query in e["date"]
        or query in e["user"].lower()
    ]
 
    if not results:
        await update.message.reply_text(f"🔍 По запросу «{query}» ничего не найдено.")
        return
 
    total = sum(e["total"] for e in results)
    lines = [f"🔍 *Результаты по «{query}»:* ({len(results)} трат, {total:.0f}р)\n"]
    for e in results:
        lines.append(f"👤 {e['user']} | {e['date']}")
        lines.append(format_expense_line(e))
        lines.append("")
 
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
 
async def cmd_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    text = format_shopping_list(data["shopping_list"])
 
    keyboard = []
    for i, item in enumerate(data["shopping_list"]):
        if not item.get("done"):
            keyboard.append([InlineKeyboardButton(
                f"✅ {item['name']}", callback_data=f"bought_{i}"
            )])
    if keyboard:
        keyboard.append([InlineKeyboardButton("🗑 Очистить купленное", callback_data="clear_done")])
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown")
 
async def cmd_add_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item_text = " ".join(context.args).strip()
    if not item_text:
        await update.message.reply_text("🛒 Использование: /купить [товар]\nПример: /купить шпаклёвку и гвозди")
        return
 
    username = update.effective_user.first_name or update.effective_user.username
    data = load_data()
 
    items = [i.strip() for i in re.split(r"\s+и\s+|,", item_text) if i.strip()]
    for item in items:
        data["shopping_list"].append({"name": item, "added_by": username, "done": False})
 
    save_data(data)
    names = ", ".join(items)
    await update.message.reply_text(f"✅ Добавлено в список: *{names}*", parse_mode="Markdown")
 
async def cmd_mark_bought(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /купил [номер]\nПример: /купил 2")
        return
    try:
        idx = int(context.args[0]) - 1
        data = load_data()
        if 0 <= idx < len(data["shopping_list"]):
            data["shopping_list"][idx]["done"] = True
            save_data(data)
            name = data["shopping_list"][idx]["name"]
            await update.message.reply_text(f"✅ *{name}* отмечено как купленное!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Неверный номер. Проверь /список")
    except ValueError:
        await update.message.reply_text("❌ Укажи номер. Например: /купил 2")
 
async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.first_name or update.effective_user.username
    data = load_data()
 
    user_expenses = [(i, e) for i, e in enumerate(data["expenses"]) if e["user"] == username]
    if not user_expenses:
        await update.message.reply_text("❌ У тебя нет трат для отмены.")
        return
 
    last_idx, last_exp = user_expenses[-1]
    data["expenses"].pop(last_idx)
    save_data(data)
    await update.message.reply_text(
        f"↩️ Отменено: *{last_exp['name']}* — {last_exp['total']:.0f}р",
        parse_mode="Markdown"
    )
 
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
 
    if query.data.startswith("bought_"):
        idx = int(query.data.split("_")[1])
        if 0 <= idx < len(data["shopping_list"]):
            data["shopping_list"][idx]["done"] = True
            save_data(data)
            await query.edit_message_text(
                format_shopping_list(data["shopping_list"]),
                parse_mode="Markdown"
            )
    elif query.data == "clear_done":
        data["shopping_list"] = [i for i in data["shopping_list"] if not i.get("done")]
        save_data(data)
        await query.edit_message_text(
            format_shopping_list(data["shopping_list"]),
            parse_mode="Markdown"
        )
 
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    username = update.effective_user.first_name or update.effective_user.username
 
    if not re.search(r"\d", text):
        return
 
    thinking_msg = await update.message.reply_text("🤔 Разбираю трату...")
 
    try:
        items = parse_expenses_with_ai(text, username)
        if not items:
            await thinking_msg.edit_text("❓ Не смог распознать трату. Попробуй написать иначе.")
            return
 
        data = load_data()
        data["expenses"].extend(items)
        save_data(data)
 
        total = sum(i["total"] for i in items)
        count = len(items)
        word = "трату" if count == 1 else "траты" if count < 5 else "трат"
        lines = [f"✅ *{username}* добавил {count} {word} на *{total:.0f}р*:\n"]
        for item in items:
            lines.append(format_expense_line(item))
 
        await thinking_msg.edit_text("\n".join(lines), parse_mode="Markdown")
 
    except Exception as e:
        await thinking_msg.edit_text(f"❌ Ошибка при разборе: {str(e)[:100]}")
 
async def monthly_report_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    data = load_data()
    today = date.today()
    if today.month == 1:
        month = f"{today.year - 1}-12"
    else:
        month = f"{today.year}-{today.month - 1:02d}"
    report = format_monthly_report(data["expenses"], month)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📅 *Автоматический итог месяца!*\n\n{report}",
        parse_mode="Markdown"
    )
 
async def cmd_setup_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_queue = context.job_queue
    current_jobs = job_queue.get_jobs_by_name(f"monthly_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    job_queue.run_monthly(
        monthly_report_job,
        when=datetime.now().replace(day=1, hour=9, minute=0, second=0),
        chat_id=chat_id,
        name=f"monthly_{chat_id}",
    )
    await update.message.reply_text("✅ Ежемесячный отчёт настроен! Буду присылать 1-го числа в 09:00.")
 
# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
 
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("mine", cmd_mine))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("list", cmd_shopping_list))
    app.add_handler(CommandHandler("buy", cmd_add_to_list))
    app.add_handler(CommandHandler("bought", cmd_mark_bought))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CommandHandler("automonth", cmd_setup_monthly))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
 
    print("🤖 Бот запущен!")
    app.run_polling()
 
if __name__ == "__main__":
    main()
