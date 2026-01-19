import os
import sqlite3
import secrets
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from flask import Flask, request
import threading

# ============================================================================
# CONFIGURATION
# ============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
RENDER_URL = os.getenv("RENDER_URL")
ADMIN_USER_ID = TELEGRAM_CHAT_ID  # Only this user can generate keys

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# DATABASE SETUP
# ============================================================================

DB_FILE = "subscription.db"

def init_database():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Keys table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT UNIQUE NOT NULL,
            duration TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            redeemed_by_name TEXT,
            redeemed_by_username TEXT,
            redeemed_by_id INTEGER,
            redemption_date TIMESTAMP,
            expiration_date TIMESTAMP
        )
    """)
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            key_code TEXT,
            redemption_date TIMESTAMP,
            expiration_date TIMESTAMP,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (key_code) REFERENCES keys(key_code)
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

# ============================================================================
# KEY MANAGEMENT FUNCTIONS
# ============================================================================

def generate_key(duration):
    """Generate a cryptographically secure unique key code"""
    # Generate 32-character random key using secure random
    # Uses uppercase letters, lowercase letters, and digits
    import string
    characters = string.ascii_letters + string.digits
    key = ''.join(secrets.choice(characters) for _ in range(32))
    return key

def create_key(duration_str):
    """Create a new key in database"""
    key_code = generate_key(duration_str)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO keys (key_code, duration, status)
        VALUES (?, ?, 'active')
    """, (key_code, duration_str))
    
    conn.commit()
    conn.close()
    return key_code

def redeem_key(key_code, user_id, username, first_name):
    """Redeem a key for a user"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Check if key exists and is active
    cursor.execute("SELECT * FROM keys WHERE key_code = ? AND status = 'active'", (key_code,))
    key = cursor.fetchone()
    
    if not key:
        conn.close()
        return False, "❌ Invalid or already used key"
    
    # Check if user already has active subscription
    cursor.execute("SELECT * FROM users WHERE user_id = ? AND status = 'active'", (user_id,))
    existing_user = cursor.fetchone()
    
    if existing_user:
        conn.close()
        return False, "❌ You already have an active subscription"
    
    # Calculate expiration date
    duration_str = key[2]  # duration field
    if duration_str == "24h":
        expiration = datetime.now() + timedelta(hours=24)
    elif duration_str == "7d":
        expiration = datetime.now() + timedelta(days=7)
    elif duration_str == "30d":
        expiration = datetime.now() + timedelta(days=30)
    else:
        conn.close()
        return False, "❌ Invalid key duration"
    
    # Update key as used
    cursor.execute("""
        UPDATE keys 
        SET status = 'used', 
            redeemed_by_name = ?, 
            redeemed_by_username = ?, 
            redeemed_by_id = ?,
            redemption_date = CURRENT_TIMESTAMP,
            expiration_date = ?
        WHERE key_code = ?
    """, (first_name, username, user_id, expiration.isoformat(), key_code))
    
    # Add user to users table
    cursor.execute("""
        INSERT INTO users (user_id, username, first_name, key_code, redemption_date, expiration_date, status)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, 'active')
    """, (user_id, username, first_name, key_code, expiration.isoformat()))
    
    conn.commit()
    conn.close()
    
    return True, f"✅ Key redeemed successfully! Expires: {expiration.strftime('%b %d, %Y at %H:%M')}"

def check_subscription(user_id):
    """Check if user has active subscription"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT expiration_date, status FROM users 
        WHERE user_id = ? AND status = 'active'
    """, (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return False, None
    
    expiration_date = datetime.fromisoformat(result[0])
    now = datetime.now()
    
    if expiration_date < now:
        # Subscription expired, update status
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status = 'expired' WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return False, None
    
    remaining = expiration_date - now
    days = remaining.days
    hours = remaining.seconds // 3600
    minutes = (remaining.seconds % 3600) // 60
    
    return True, {
        "expiration": expiration_date,
        "days": days,
        "hours": hours,
        "minutes": minutes
    }

def get_all_keys():
    """Get all keys for admin panel"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT key_code, duration, status, redeemed_by_name, redeemed_by_username, expiration_date
        FROM keys
        ORDER BY created_date DESC
    """)
    
    keys = cursor.fetchall()
    conn.close()
    return keys

def revoke_key(key_code):
    """Revoke a key"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("UPDATE keys SET status = 'revoked' WHERE key_code = ?", (key_code,))
    cursor.execute("UPDATE users SET status = 'revoked' WHERE key_code = ?", (key_code,))
    
    conn.commit()
    conn.close()

def suspend_user(user_id):
    """Suspend a user"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("UPDATE users SET status = 'suspended' WHERE user_id = ?", (user_id,))
    
    conn.commit()
    conn.close()

# ============================================================================
# TELEGRAM BOT HANDLERS
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user = update.effective_user
    message = f"""
🤖 **Welcome to PayPal Fraud Prevention Bot**

This bot allows you to make automated calls for fraud prevention verification.

**Available Commands:**
• `/redeem <key>` - Redeem your subscription key
• `/subscription` - Check your subscription status
• `/call <number> <from> <name> <service>` - Make a call (requires active subscription)
• `/help` - Show help

**Admin Commands (Authorized users only):**
• `/genkey <duration>` - Generate a key (24h, 7d, 30d)
• `/admin` - View admin panel
• `/revoke <key>` - Revoke a key
• `/suspend <user_id>` - Suspend a user

For more information, use `/help`
    """
    await update.message.reply_text(message, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    message = """
📖 **HELP GUIDE**

**For Users:**

1️⃣ **Redeem a Key**
   `/redeem YOUR_KEY_CODE`
   
2️⃣ **Check Subscription**
   `/subscription`
   
3️⃣ **Make a Call**
   `/call +1234567890 +12566967661 John Smith PayPal`
   
   Format: `/call <customer_number> <your_number> <customer_name> <service_name>`

**For Admin:**

1️⃣ **Generate Keys**
   `/genkey 24h` - 24-hour key
   `/genkey 7d` - 7-day key
   `/genkey 30d` - 30-day key
   
2️⃣ **View Admin Panel**
   `/admin` - See all keys and users
   
3️⃣ **Revoke a Key**
   `/revoke KEY_CODE`
   
4️⃣ **Suspend a User**
   `/suspend USER_ID`
    """
    await update.message.reply_text(message, parse_mode="Markdown")

async def genkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate a new key (admin only)"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized. Only the admin can generate keys.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/genkey <24h|7d|30d>`", parse_mode="Markdown")
        return
    
    duration = context.args[0].lower()
    
    if duration not in ["24h", "7d", "30d"]:
        await update.message.reply_text("❌ Invalid duration. Use: 24h, 7d, or 30d")
        return
    
    key_code = create_key(duration)
    
    message = f"""
✅ **Key Generated Successfully!**

🔑 **Key Code:** `{key_code}`
⏱️ **Duration:** {duration}
📅 **Created:** {datetime.now().strftime('%b %d, %Y at %H:%M')}

Share this key with users. They can redeem it with:
`/redeem {key_code}`
    """
    await update.message.reply_text(message, parse_mode="Markdown")
    
    # Log to admin
    logger.info(f"Key generated: {key_code} ({duration})")

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem a key"""
    user = update.effective_user
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/redeem YOUR_KEY_CODE`", parse_mode="Markdown")
        return
    
    key_code = context.args[0].upper()
    success, message = redeem_key(key_code, user.id, user.username or "Unknown", user.first_name)
    
    if success:
        await update.message.reply_text(message, parse_mode="Markdown")
        # Notify admin
        admin_msg = f"✅ User {user.first_name} (@{user.username}) redeemed key: {key_code}"
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=admin_msg)
    else:
        await update.message.reply_text(message)

async def subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check subscription status"""
    user = update.effective_user
    
    has_sub, sub_info = check_subscription(user.id)
    
    if not has_sub:
        await update.message.reply_text(
            "❌ No active subscription. Use `/redeem <key>` to activate.",
            parse_mode="Markdown"
        )
        return
    
    message = f"""
✅ **Active Subscription**

👤 **User:** {user.first_name}
📅 **Expires:** {sub_info['expiration'].strftime('%b %d, %Y at %H:%M')}
⏱️ **Time Remaining:** {sub_info['days']} days, {sub_info['hours']} hours, {sub_info['minutes']} minutes
    """
    await update.message.reply_text(message, parse_mode="Markdown")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel"""
    user = update.effective_user
    
    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized. Only the admin can access this.")
        return
    
    keys = get_all_keys()
    
    if not keys:
        await update.message.reply_text("📊 **Admin Panel**\n\nNo keys found.", parse_mode="Markdown")
        return
    
    message = "📊 **Admin Panel - All Keys**\n\n"
    
    for key in keys:
        key_code, duration, status, redeemed_by_name, redeemed_by_username, expiration_date = key
        
        status_emoji = "🟢" if status == "active" else "🔴" if status == "used" else "⛔"
        
        if status == "used":
            message += f"{status_emoji} `{key_code}` ({duration})\n"
            message += f"   User: {redeemed_by_name} (@{redeemed_by_username})\n"
            message += f"   Expires: {expiration_date}\n\n"
        else:
            message += f"{status_emoji} `{key_code}` ({duration}) - {status.upper()}\n\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke a key"""
    user = update.effective_user
    
    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/revoke KEY_CODE`", parse_mode="Markdown")
        return
    
    key_code = context.args[0].upper()
    revoke_key(key_code)
    
    await update.message.reply_text(f"✅ Key `{key_code}` has been revoked.", parse_mode="Markdown")

async def suspend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Suspend a user"""
    user = update.effective_user
    
    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/suspend USER_ID`", parse_mode="Markdown")
        return
    
    try:
        user_id = int(context.args[0])
        suspend_user(user_id)
        await update.message.reply_text(f"✅ User `{user_id}` has been suspended.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def call_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Make a call (requires active subscription)"""
    user = update.effective_user
    
    # Check subscription
    has_sub, sub_info = check_subscription(user.id)
    
    if not has_sub:
        await update.message.reply_text(
            "❌ No active subscription. Use `/redeem <key>` to make calls.",
            parse_mode="Markdown"
        )
        return
    
    if len(context.args) < 4:
        await update.message.reply_text(
            "Usage: `/call <customer_number> <your_number> <customer_name> <service_name>`",
            parse_mode="Markdown"
        )
        return
    
    customer_number = context.args[0]
    our_number = context.args[1]
    customer_name = context.args[2]
    service_name = " ".join(context.args[3:])
    
    # Send notification
    await update.message.reply_text(
        f"📞 Initiating call...\n\nFrom: {our_number}\nTo: {customer_number}\nCustomer: {customer_name}\nService: {service_name}"
    )
    
    try:
        # Initialize Twilio client
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Make the call
        call = twilio_client.calls.create(
            to=customer_number,
            from_=our_number,
            url=f"{RENDER_URL}/voice"
        )
        
        await update.message.reply_text(f"✅ Call initiated! Call SID: {call.sid}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error initiating call:\n\n{str(e)}")

# ============================================================================
# FLASK SERVER FOR TWILIO WEBHOOKS
# ============================================================================

app = Flask(__name__)

@app.route("/voice", methods=["POST"])
def voice():
    """Handle incoming calls"""
    response = VoiceResponse()
    response.say("Hello! This is the PayPal Fraud Prevention Line.")
    return str(response)

# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    """Start the bot"""
    # Initialize database
    init_database()
    
    # Check for required environment variables
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, RENDER_URL]):
        logger.error("❌ ERROR: Missing required environment variables!")
        return
    
    logger.info("✅ Starting Telegram bot...")
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("genkey", genkey))
    application.add_handler(CommandHandler("redeem", redeem))
    application.add_handler(CommandHandler("subscription", subscription))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("revoke", revoke))
    application.add_handler(CommandHandler("suspend", suspend))
    application.add_handler(CommandHandler("call", call_command))
    
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000, debug=False))
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start polling
    application.run_polling()

if __name__ == "__main__":
    main()
