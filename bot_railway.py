# Updated Apr 23#!/usr/bin/env python3
"""
Telegram Spam Filter Bot for Railway
- Auto-accepts join requests
- Instant kicks forwarded messages (unless from admin)
- Instant kicks messages with links (except admin)
- Kicks messages with 4+ emojis
- Kicks rapid-fire duplicate messages (5+ in 10 seconds)
- Notifies admin group when kicks happen
- Reads configuration from environment variables
"""

from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, ContextTypes, filters, ChatMemberHandler
from datetime import datetime, timedelta
import logging
import os

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION - READ FROM ENVIRONMENT VARIABLES
# ============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONITORED_GROUPS = os.getenv("MONITORED_GROUPS", "").split(",")
MONITORED_GROUPS = [int(g.strip()) for g in MONITORED_GROUPS if g.strip()]
ADMIN_LOG_GROUP = int(os.getenv("ADMIN_LOG_GROUP", "0")) if os.getenv("ADMIN_LOG_GROUP") else None

# Thresholds
NEW_ACCOUNT_THRESHOLD_DAYS = 7
EMOJI_SPAM_THRESHOLD = 4
RAPID_FIRE_THRESHOLD_SECONDS = 10
RAPID_FIRE_MESSAGE_COUNT = 5

user_message_history = {}

# ============================================================================
# VALIDATION
# ============================================================================

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN environment variable not set!")
    exit(1)

if not MONITORED_GROUPS:
    logger.error("❌ MONITORED_GROUPS environment variable not set!")
    exit(1)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def is_admin(context: ContextTypes.DEFAULT_TYPE, group_id: int, user_id: int) -> bool:
    """Check if user is admin/moderator in the group"""
    try:
        member = await context.bot.get_chat_member(group_id, user_id)
        return member.status in ['administrator', 'creator']
    except:
        return False

def count_emojis(text: str) -> int:
    """Count emoji characters in text"""
    emoji_count = 0
    for char in text:
        if ord(char) > 0x1F000 and ord(char) < 0x1F999:
            emoji_count += 1
    return emoji_count

def has_heavy_emojis(text: str) -> bool:
    """Check if text has excessive emojis (spam indicator)"""
    return count_emojis(text) >= EMOJI_SPAM_THRESHOLD

def has_link(message) -> bool:
    """Check if message contains any links"""
    if message.entities:
        return any(entity.type in ['url', 'text_link'] for entity in message.entities)
    return False

def check_rapid_fire_duplicates(user_id: int, message_text: str) -> bool:
    """Check if user posted same message 5+ times in 10 seconds"""
    now = datetime.now()
    
    if user_id not in user_message_history:
        user_message_history[user_id] = []
    
    history = user_message_history[user_id]
    history[:] = [(text, ts) for text, ts in history if (now - ts).total_seconds() < RAPID_FIRE_THRESHOLD_SECONDS]
    
    duplicate_count = sum(1 for text, ts in history if text == message_text)
    history.append((message_text, now))
    
    return duplicate_count >= RAPID_FIRE_MESSAGE_COUNT

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """Send notification to admin log group"""
    if ADMIN_LOG_GROUP:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_LOG_GROUP,
                text=message_text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Failed to notify admins: {e}")

# ============================================================================
# MESSAGE HANDLERS
# ============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monitor messages for spam patterns"""
    message = update.message
    
    if message.chat_id not in MONITORED_GROUPS:
        return
    
    user = message.from_user
    group_id = message.chat_id
    user_id = user.id
    message_text = message.text or ""
    
    user_is_admin = await is_admin(context, group_id, user_id)
    
    # RULE 1: Kick if forwarded message (unless from admin)
    try:
        is_forwarded = message.forward_date or message.forward_from or message.forward_from_chat
    except:
        is_forwarded = False
    
    if is_forwarded and not user_is_admin:
        try:
            await context.bot.ban_chat_member(group_id, user_id)
            await context.bot.unban_chat_member(group_id, user_id)
            await message.delete()
            
            log_msg = (
                f"⚠️ <b>SPAM KICK - FORWARDED MESSAGE</b>\n"
                f"User: {user.mention_html()}\n"
                f"ID: <code>{user_id}</code>\n"
                f"Group: {message.chat.title}\n"
                f"Reason: Forwarded from external channel\n"
                f"Status: Can rejoin\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await notify_admins(context, log_msg)
            logger.info(f"Kicked {user_id} for forwarding in {group_id}")
            return
        except Exception as e:
            logger.error(f"Failed to kick user {user_id}: {e}")
    
    # RULE 2: Kick any message with links (except from admins)
    if has_link(message) and not user_is_admin:
        try:
            await context.bot.ban_chat_member(group_id, user_id)
            await context.bot.unban_chat_member(group_id, user_id)
            await message.delete()
            
            log_msg = (
                f"⚠️ <b>SPAM KICK - LINK POSTED</b>\n"
                f"User: {user.mention_html()}\n"
                f"ID: <code>{user_id}</code>\n"
                f"Group: {message.chat.title}\n"
                f"Reason: Non-admin posted external link\n"
                f"Status: Can rejoin\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await notify_admins(context, log_msg)
            logger.info(f"Kicked {user_id} for posting link in {group_id}")
            return
        except Exception as e:
            logger.error(f"Failed to kick user {user_id}: {e}")
    
    # RULE 3: Kick if heavy emoji usage (spam indicator)
    if has_heavy_emojis(message_text) and not user_is_admin:
        try:
            emoji_count = count_emojis(message_text)
            await context.bot.ban_chat_member(group_id, user_id)
            await context.bot.unban_chat_member(group_id, user_id)
            await message.delete()
            
            log_msg = (
                f"⚠️ <b>SPAM KICK - HEAVY EMOJI USAGE</b>\n"
                f"User: {user.mention_html()}\n"
                f"ID: <code>{user_id}</code>\n"
                f"Group: {message.chat.title}\n"
                f"Emoji Count: {emoji_count}\n"
                f"Status: Can rejoin\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await notify_admins(context, log_msg)
            logger.info(f"Kicked {user_id} for heavy emoji usage in {group_id}")
            return
        except Exception as e:
            logger.error(f"Failed to kick user {user_id}: {e}")
    
    # RULE 4: Kick if rapid-fire duplicate messages (5+ in 10 seconds)
    if check_rapid_fire_duplicates(user_id, message_text) and not user_is_admin:
        try:
            await context.bot.ban_chat_member(group_id, user_id)
            await context.bot.unban_chat_member(group_id, user_id)
            await message.delete()
            
            log_msg = (
                f"⚠️ <b>SPAM KICK - RAPID-FIRE DUPLICATES</b>\n"
                f"User: {user.mention_html()}\n"
                f"ID: <code>{user_id}</code>\n"
                f"Group: {message.chat.title}\n"
                f"Pattern: 5+ identical messages in 10 seconds\n"
                f"Status: Can rejoin\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await notify_admins(context, log_msg)
            logger.info(f"Kicked {user_id} for rapid-fire spam in {group_id}")
            return
        except Exception as e:
            logger.error(f"Failed to kick user {user_id}: {e}")

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-approve join requests"""
    chat_member_update = update.chat_member
    
    if chat_member_update.new_chat_member.status == 'restricted':
        user = chat_member_update.from_user
        group_id = chat_member_update.chat.id
        
        try:
            await context.bot.approve_chat_join_request(
                chat_id=group_id,
                user_id=user.id
            )
            logger.info(f"Auto-approved join request from {user.id} for group {group_id}")
        except Exception as e:
            logger.error(f"Failed to approve join request: {e}")

# ============================================================================
# MAIN BOT SETUP
# ============================================================================

async def post_init(application: Application) -> None:
    """Print startup info"""
    print("\n" + "="*60)
    print("✅ KING XAVIER BOT STARTED (Railway)")
    print("="*60)
    print(f"Monitoring groups: {MONITORED_GROUPS}")
    print(f"Admin log group: {ADMIN_LOG_GROUP}")
    print("\n📋 DETECTION RULES:")
    print(f"  • Forwarded messages (kicked - can rejoin)")
    print(f"  • ANY links from non-admins (kicked - can rejoin)")
    print(f"  • Heavy emoji usage: {EMOJI_SPAM_THRESHOLD}+ emojis (kicked - can rejoin)")
    print(f"  • Rapid-fire duplicates: {RAPID_FIRE_MESSAGE_COUNT}+ messages in {RAPID_FIRE_THRESHOLD_SECONDS}s (kicked - can rejoin)")
    print(f"\n✅ Admins are exempt from all rules")
    print("="*60 + "\n")

def main():
    """Start the bot"""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    application.add_handler(MessageHandler(filters.ALL, handle_message))
    application.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    
    print("Starting bot...\n")
    application.run_polling(allowed_updates=["message", "chat_member"])

if __name__ == '__main__':
    main()
