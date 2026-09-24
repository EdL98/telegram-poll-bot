import os
import uuid
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

MAX_VOTES = 3

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --------------------------------------------------
# STORAGE (Memory)
# --------------------------------------------------

# poll_id -> poll object
polls = {}

# "chat_id:message_id" -> poll_id
message_links = {}


# --------------------------------------------------
# KEYBOARD
# --------------------------------------------------

def build_keyboard(poll):
    keyboard = []

    for i, option in enumerate(poll["options"]):
        count = len(poll["votes"][i])

        keyboard.append([
            InlineKeyboardButton(
                f"{option} ({count})",
                callback_data=f"vote:{poll['id']}:{i}"
            )
        ])

    return InlineKeyboardMarkup(keyboard)


async def refresh_poll_everywhere(context, poll):
    """Update every copy of the poll across all chats."""
    for chat_id, message_id in poll["messages"]:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=build_keyboard(poll)
            )
        except Exception as e:
            logger.warning(
                f"Unable to update message {chat_id}:{message_id}: {e}"
            )


# --------------------------------------------------
# /newpoll
# --------------------------------------------------

async def newpoll(update: Update, context: ContextTypes.DEFAULT_TYPE):

    raw = update.message.text.partition(" ")[2]
    parts = [p.strip() for p in raw.split("|") if p.strip()]

    if len(parts) < 3:
        await update.message.reply_text(
            "Usage:\n"
            "/newpoll Question | Option 1 | Option 2 | Option 3 ..."
        )
        return

    question = parts[0]
    options = parts[1:]

    poll_id = uuid.uuid4().hex[:6].upper()

    poll = {
        "id": poll_id,
        "question": question,
        "options": options,
        "votes": {i: set() for i in range(len(options))},
        "user_selections": {},
        "messages": [],
        "closed": False,
    }

    polls[poll_id] = poll

    sent = await update.message.reply_text(
        f"📊 *{question}*\n\n"
        f"**Poll ID:** `{poll_id}`\n"
        f"Pick up to *{MAX_VOTES}* options.",
        parse_mode="Markdown",
        reply_markup=build_keyboard(poll),
    )

    poll["messages"].append((sent.chat_id, sent.message_id))
    message_links[f"{sent.chat_id}:{sent.message_id}"] = poll_id


# --------------------------------------------------
# Voting
# --------------------------------------------------

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    _, poll_id, option = query.data.split(":")
    option_index = int(option)

    poll = polls.get(poll_id)

    if poll is None:
        await query.answer("Poll not found.", show_alert=True)
        return

    if poll["closed"]:
        await query.answer("This poll is closed.", show_alert=True)
        return

    user_id = query.from_user.id

    selections = poll["user_selections"].setdefault(user_id, set())

    # Remove vote
    if option_index in selections:
        selections.remove(option_index)
        poll["votes"][option_index].discard(user_id)

        await query.answer(
            f"Removed: {poll['options'][option_index]}"
        )

    # Add vote
    else:

        if len(selections) >= MAX_VOTES:
            await query.answer(
                f"You've already selected {MAX_VOTES} options.\n"
                "Tap one of your existing choices again to remove it.",
                show_alert=True,
            )
            return

        selections.add(option_index)
        poll["votes"][option_index].add(user_id)

        remaining = MAX_VOTES - len(selections)

        await query.answer(
            f"Added: {poll['options'][option_index]}\n"
            f"{remaining} pick(s) remaining."
        )

    await refresh_poll_everywhere(context, poll)


# --------------------------------------------------
# /polls
# --------------------------------------------------

async def list_polls(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not polls:
        await update.message.reply_text("No active polls.")
        return

    lines = ["📋 *Active Polls*\n"]

    for poll in polls.values():
        total_votes = sum(len(v) for v in poll["votes"].values())
        status = "🔒 Closed" if poll["closed"] else "🟢 Open"

        lines.append(
            f"*{poll['id']}* — {poll['question']}\n"
            f"{status} | {total_votes} vote(s)"
        )

    lines.append("\nUse `/viewpoll POLL_ID` to display a poll here.")

    await update.message.reply_text(
        "\n\n".join(lines),
        parse_mode="Markdown",
    )


# --------------------------------------------------
# /viewpoll POLL_ID
# --------------------------------------------------

async def viewpoll(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/viewpoll POLL_ID"
        )
        return

    poll_id = context.args[0].upper()

    poll = polls.get(poll_id)

    if poll is None:
        await update.message.reply_text("Poll not found.")
        return

    sent = await update.message.reply_text(
        f"📊 *{poll['question']}*\n\n"
        f"**Poll ID:** `{poll_id}`\n"
        f"Pick up to *{MAX_VOTES}* options.",
        parse_mode="Markdown",
        reply_markup=build_keyboard(poll),
    )

    poll["messages"].append((sent.chat_id, sent.message_id))
    message_links[f"{sent.chat_id}:{sent.message_id}"] = poll_id


# --------------------------------------------------
# /results
# --------------------------------------------------

async def results(update: Update, context: ContextTypes.DEFAULT_TYPE):

    poll = None

    # /results ABC123
    if context.args:
        poll = polls.get(context.args[0].upper())

    # Reply to poll message
    elif update.message.reply_to_message:
        key = (
            f"{update.message.chat_id}:"
            f"{update.message.reply_to_message.message_id}"
        )

        poll_id = message_links.get(key)

        if poll_id:
            poll = polls.get(poll_id)

    # Latest poll
    elif polls:
        poll = list(polls.values())[-1]

    if poll is None:
        await update.message.reply_text("Poll not found.")
        return

    lines = [
        f"📊 *{poll['question']}*",
        f"Poll ID: `{poll['id']}`",
        "",
    ]

    total_unique_voters = len(poll["user_selections"])

    for i, option in enumerate(poll["options"]):
        count = len(poll["votes"][i])
        lines.append(f"• {option}: *{count}* vote(s)")

    lines.append("")
    lines.append(f"👥 Total Participants: *{total_unique_voters}*")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# --------------------------------------------------
# /closepoll
# --------------------------------------------------

async def closepoll(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/closepoll POLL_ID"
        )
        return

    poll_id = context.args[0].upper()

    poll = polls.get(poll_id)

    if poll is None:
        await update.message.reply_text("Poll not found.")
        return

    poll["closed"] = True

    await refresh_poll_everywhere(context, poll)

    await update.message.reply_text(
        f"🔒 Poll `{poll_id}` has been closed.",
        parse_mode="Markdown",
    )


# --------------------------------------------------
# /deletepoll
# --------------------------------------------------

async def deletepoll(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/deletepoll POLL_ID"
        )
        return

    poll_id = context.args[0].upper()

    poll = polls.pop(poll_id, None)

    if poll is None:
        await update.message.reply_text("Poll not found.")
        return

    # Remove mapping
    for chat_id, message_id in poll["messages"]:
        message_links.pop(f"{chat_id}:{message_id}", None)

    await update.message.reply_text(
        f"🗑 Poll `{poll_id}` deleted.",
        parse_mode="Markdown",
    )


# --------------------------------------------------
# START BOT
# --------------------------------------------------

def main():

    token = os.environ.get(
        "BOT_TOKEN",
        "8657846432:AAEoc9x4DT3zRd2WHtcXWkG2VI-eFRQa20o"
    )

    if token == "YOUR_TELEGRAM_BOT_TOKEN":
        raise SystemExit(
            "Please set BOT_TOKEN environment variable or replace "
            "'YOUR_TELEGRAM_BOT_TOKEN' with your bot token."
        )

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("newpoll", newpoll))
    app.add_handler(CommandHandler("polls", list_polls))
    app.add_handler(CommandHandler("viewpoll", viewpoll))
    app.add_handler(CommandHandler("results", results))
    app.add_handler(CommandHandler("closepoll", closepoll))
    app.add_handler(CommandHandler("deletepoll", deletepoll))

    app.add_handler(
        CallbackQueryHandler(handle_vote, pattern=r"^vote:")
    )

    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()