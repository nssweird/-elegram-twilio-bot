import os
import sqlite3
import secrets
import string
import uuid
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from twilio.rest import Client
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
RENDER_URL = os.getenv("RENDER_URL")

# Admin user ID (your Telegram user ID)
ADMIN_USER_ID = 6649480605  # Replace with your actual user ID

# Twilio client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Database setup
DB_FILE = "bot_data.db"

def init_database():
    """Initialize SQLite database with proper schema"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create keys table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT UNIQUE NOT NULL,
            duration TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            redeemed_by_user_id INTEGER,
            redeemed_by_name TEXT,
            redeemed_by_username TEXT,
            redeemed_at TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)
    
    # Create users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE NOT NULL,
            telegram_id INTEGER UNIQUE NOT NULL,
            telegram_name TEXT,
            telegram_username TEXT,
            key_code TEXT,
            subscription_status TEXT DEFAULT 'active',
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

def generate_user_id():
    """Generate a unique professional User ID using UUID for guaranteed uniqueness"""
    # Use UUID to ensure absolute uniqueness without blocking database checks
    unique_part = str(uuid.uuid4()).split('-')[0].upper()[:6]
    return f"USR_{unique_part}"

def generate_key(duration):
    """Generate a secure random key"""
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))

def get_expiration_time(duration):
    """Calculate expiration time based on duration"""
    now = datetime.now()
    if duration == "24h":
        return now + timedelta(hours=24)
    elif duration == "7d":
        return now + timedelta(days=7)
    elif duration == "30d":
        return now + timedelta(days=30)
    return now

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - show different help based on user role"""
    user_id = update.effective_user.id
    is_admin = user_id == ADMIN_USER_ID
    
    if is_admin:
        welcome_text = """
👋 **Welcome Admin!**

You have full access to all commands.

**For Admin:**

1️⃣ **Generate Keys**
   `/genkey 24h` - 24-hour key
   `/genkey 7d` - 7-day key
   `/genkey 30d` - 30-day key
   
2️⃣ **View Admin Panel**
   `/admin` - See all keys and users
   
3️⃣ **Revoke a Key**
   `/revoke KEY_CODE` or `/revoke @username` or `/revoke USER_ID`
   
4️⃣ **Suspend a User**
   `/suspend KEY_CODE` or `/suspend @username` or `/suspend USER_ID`
   
5️⃣ **Unsuspend a User**
   `/unsuspend KEY_CODE` or `/unsuspend @username` or `/unsuspend USER_ID`

**For Users:**

1️⃣ **Redeem a Key**
   `/redeem KEY_CODE`
   
2️⃣ **Check Subscription**
   `/subscription`
   
3️⃣ **Make a Call**
   `/call <customer_number> <your_number> <customer_name> <service_name>`
"""
    else:
        welcome_text = """
👋 **Welcome!**

**Available Commands:**

1️⃣ **Redeem a Key**
   `/redeem KEY_CODE`
   
2️⃣ **Check Subscription**
   `/subscription`
   
3️⃣ **Make a Call**
   `/call <customer_number> <your_number> <customer_name> <service_name>`

4️⃣ **Get Help**
   `/help`
"""
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command - show different help based on user role"""
    user_id = update.effective_user.id
    is_admin = user_id == ADMIN_USER_ID
    
    if is_admin:
        help_text = """
📖 **Help - Admin Commands**

**Key Management:**
• `/genkey 24h` - Generate 24-hour key
• `/genkey 7d` - Generate 7-day key
• `/genkey 30d` - Generate 30-day key
• `/admin` - View all keys and users
• `/revoke KEY_CODE` - Revoke a specific key
• `/revoke @username` - Revoke user's key by username

**User Management:**
• `/suspend USER_ID` - Suspend a user
• `/suspend @username` - Suspend user by username
• `/unsuspend USER_ID` - Unsuspend a user
• `/unsuspend @username` - Unsuspend user by username

**User Commands:**
• `/redeem KEY_CODE` - Redeem a key
• `/subscription` - Check subscription status
• `/call` - Make a call (requires active subscription)
"""
    else:
        help_text = """
📖 **Help - User Commands**

**Subscription:**
• `/redeem KEY_CODE` - Redeem a key to activate subscription
• `/subscription` - Check your subscription status

**Calling:**
• `/call <customer_number> <your_number> <customer_name> <service_name>` - Make a call

Example:
`/call +16315127338 +12566967661 John Smith PayPal`
"""
    
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def genkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate a new key (admin only)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized. Only admin can generate keys.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/genkey 24h` or `/genkey 7d` or `/genkey 30d`", parse_mode="Markdown")
        return
    
    duration = context.args[0]
    if duration not in ["24h", "7d", "30d"]:
        await update.message.reply_text("❌ Invalid duration. Use: 24h, 7d, or 30d")
        return
    
    key_code = generate_key(duration)
    expires_at = get_expiration_time(duration)
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO keys (key_code, duration, status, expires_at)
            VALUES (?, ?, 'active', ?)
        """, (key_code, duration, expires_at))
        conn.commit()
        conn.close()
        
        response = f"""
✅ **Key Generated Successfully!**

🔑 **Key Code:** `{key_code}`
⏱️ **Duration:** {duration}
📅 **Created:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏰ **Expires:** {expires_at.strftime('%Y-%m-%d %H:%M:%S')}

**Share this key with users:**
`/redeem {key_code}`
"""
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error generating key: {e}")
        await update.message.reply_text(f"❌ Error generating key: {str(e)}")

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem a key"""
    user_id = update.effective_user.id
    telegram_name = update.effective_user.first_name
    telegram_username = update.effective_user.username or "N/A"
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/redeem KEY_CODE`", parse_mode="Markdown")
        return
    
    key_code = context.args[0]
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Check if key exists and is valid
        cursor.execute("""
            SELECT key_code, duration, status, expires_at FROM keys WHERE key_code = ?
        """, (key_code,))
        key_data = cursor.fetchone()
        
        if not key_data:
            await update.message.reply_text("❌ Invalid or already used key")
            conn.close()
            return
        
        key_code_db, duration, status, expires_at = key_data
        
        if status != "active":
            await update.message.reply_text("❌ This key has been revoked or is no longer valid")
            conn.close()
            return
        
        # Check if user already has an active subscription
        cursor.execute("""
            SELECT user_id FROM users WHERE telegram_id = ? AND subscription_status = 'active'
        """, (user_id,))
        existing_user = cursor.fetchone()
        
        if existing_user:
            await update.message.reply_text("❌ You already have an active subscription")
            conn.close()
            return
        
        # Check if user exists (revoked/suspended)
        cursor.execute("""
            SELECT user_id FROM users WHERE telegram_id = ?
        """, (user_id,))
        existing_user_record = cursor.fetchone()
        
        if existing_user_record:
            # User exists but is revoked/suspended - UPDATE their record
            unique_user_id = existing_user_record[0]
            cursor.execute("""
                UPDATE users 
                SET key_code = ?, subscription_status = 'active', expires_at = ?, telegram_name = ?, telegram_username = ?
                WHERE telegram_id = ?
            """, (key_code, expires_at, telegram_name, telegram_username, user_id))
        else:
            # New user - CREATE a new record
            unique_user_id = generate_user_id()
            cursor.execute("""
                INSERT INTO users (user_id, telegram_id, telegram_name, telegram_username, key_code, subscription_status, expires_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
            """, (unique_user_id, user_id, telegram_name, telegram_username, key_code, expires_at))
        
        # Mark key as used
        cursor.execute("""
            UPDATE keys SET status = 'used', redeemed_by_user_id = ?, redeemed_by_name = ?, redeemed_by_username = ?, redeemed_at = CURRENT_TIMESTAMP
            WHERE key_code = ?
        """, (user_id, telegram_name, telegram_username, key_code))
        
        conn.commit()
        conn.close()
        
        response = f"""
✅ **Key Redeemed Successfully!**

👤 **Your Profile:**
   User: {telegram_name} (@{telegram_username})
   **User ID:** `{unique_user_id}`

⏰ **Subscription Active:**
   Duration: {duration}
   Expires: {datetime.fromisoformat(expires_at).strftime('%Y-%m-%d %H:%M:%S')}

🎯 **Next Steps:**
   • Use `/subscription` to check remaining time
   • Use `/call` to make calls

**Your User ID:** `{unique_user_id}` (Save this for reference)
"""
        await update.message.reply_text(response, parse_mode="Markdown")
        
        # Notify admin
        admin_msg = f"""
📢 **New User Redeemed Key**

👤 User: {telegram_name} (@{telegram_username})
🆔 User ID: `{unique_user_id}`
🔑 Key: `{key_code}`
⏱️ Duration: {duration}
📅 Expires: {datetime.fromisoformat(expires_at).strftime('%Y-%m-%d %H:%M:%S')}
"""
        try:
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=admin_msg, parse_mode="Markdown")
        except:
            pass
        
    except Exception as e:
        logger.error(f"Error redeeming key: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check subscription status"""
    user_id = update.effective_user.id
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT user_id, subscription_status, expires_at, key_code FROM users WHERE telegram_id = ? ORDER BY created_at DESC LIMIT 1
        """, (user_id,))
        user_data = cursor.fetchone()
        
        conn.close()
        
        if not user_data:
            await update.message.reply_text("❌ No subscription found. Use `/redeem KEY_CODE` to activate.", parse_mode="Markdown")
            return
        
        user_id_db, status, expires_at, key_code = user_data
        expires_dt = datetime.fromisoformat(expires_at)
        remaining = expires_dt - datetime.now()
        
        if status == "suspended":
            response = "🚫 **Your subscription has been suspended.**"
        elif status == "revoked":
            response = "❌ **Your subscription has been revoked.**"
        elif remaining.total_seconds() < 0:
            response = "⏰ **Your subscription has expired.** Use `/redeem KEY_CODE` to activate a new one."
        else:
            days = remaining.days
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60
            
            response = f"""
✅ **Subscription Active**

👤 **Your ID:** `{user_id_db}`
📊 **Status:** Active
⏰ **Remaining Time:** {days}d {hours}h {minutes}m
📅 **Expires:** {expires_dt.strftime('%Y-%m-%d %H:%M:%S')}
🔑 **Key:** `{key_code}`
"""
        
        await update.message.reply_text(response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel to view all keys and users"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized. Only admin can access this panel.")
        return
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Get all keys
        cursor.execute("""
            SELECT key_code, duration, status, redeemed_by_name, redeemed_by_username, expires_at FROM keys ORDER BY created_at DESC
        """)
        keys = cursor.fetchall()
        
        # Get all users
        cursor.execute("""
            SELECT user_id, telegram_name, telegram_username, subscription_status, expires_at, key_code FROM users ORDER BY created_at DESC
        """)
        users = cursor.fetchall()
        
        conn.close()
        
        # Build response
        response = "📊 **Admin Panel - All Keys & Users**\n\n"
        
        response += "🔑 **Keys:**\n"
        if keys:
            for key_code, duration, status, name, username, expires_at in keys:
                status_emoji = "🟢" if status == "active" else "⛔️"
                if status == "active":
                    response += f"{status_emoji} `{key_code}` ({duration}) - ACTIVE\n"
                else:
                    response += f"{status_emoji} `{key_code}` ({duration}) - {status.upper()}\n"
        else:
            response += "No keys found.\n"
        
        response += "\n👥 **Users:**\n"
        if users:
            for user_id_db, name, username, sub_status, expires_at, key_code in users:
                status_emoji = "✅" if sub_status == "active" else "🚫"
                response += f"{status_emoji} **User ID:** `{user_id_db}`\n   Name: {name} (@{username})\n   Status: {sub_status}\n   Key: `{key_code}`\n   Expires: {expires_at}\n\n"
        else:
            response += "No users found.\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error accessing admin panel: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke a key or user's subscription (admin only)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized. Only admin can revoke keys.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/revoke KEY_CODE` or `/revoke @username` or `/revoke USER_ID`", parse_mode="Markdown")
        return
    
    target = context.args[0]
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Check if it's a username (starts with @)
        if target.startswith("@"):
            username = target[1:]  # Remove the @
            
            # Find user by username and revoke their key
            cursor.execute("""
                SELECT key_code FROM users WHERE telegram_username = ?
            """, (username,))
            user_result = cursor.fetchone()
            
            if user_result:
                key_code = user_result[0]
                cursor.execute("UPDATE keys SET status = 'revoked' WHERE key_code = ?", (key_code,))
                cursor.execute("UPDATE users SET subscription_status = 'revoked' WHERE telegram_username = ?", (username,))
                conn.commit()
                await update.message.reply_text(f"✅ User @{username}'s subscription has been revoked.", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"❌ User @{username} not found.", parse_mode="Markdown")
        else:
            # Check if it's a USER_ID (starts with USR_)
            if target.startswith("USR_"):
                cursor.execute("""
                    SELECT key_code FROM users WHERE user_id = ?
                """, (target,))
                user_result = cursor.fetchone()
                
                if user_result:
                    key_code = user_result[0]
                    cursor.execute("UPDATE keys SET status = 'revoked' WHERE key_code = ?", (key_code,))
                    cursor.execute("UPDATE users SET subscription_status = 'revoked' WHERE user_id = ?", (target,))
                    conn.commit()
                    await update.message.reply_text(f"✅ User `{target}`'s subscription has been revoked.", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ User `{target}` not found.", parse_mode="Markdown")
            else:
                # It's a key code
                cursor.execute("""
                    SELECT key_code FROM keys WHERE key_code = ?
                """, (target,))
                key_result = cursor.fetchone()
                
                if key_result:
                    # Revoke the key
                    cursor.execute("UPDATE keys SET status = 'revoked' WHERE key_code = ?", (target,))
                    # Also revoke the user who redeemed this key
                    cursor.execute("UPDATE users SET subscription_status = 'revoked' WHERE key_code = ?", (target,))
                    conn.commit()
                    await update.message.reply_text(f"✅ Key `{target}` and associated user have been revoked.", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ Key `{target}` not found.", parse_mode="Markdown")
        
        conn.close()
    except Exception as e:
        logger.error(f"Error revoking: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def suspend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Suspend a user (admin only)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized. Only admin can suspend users.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/suspend KEY_CODE` or `/suspend @username` or `/suspend USER_ID`", parse_mode="Markdown")
        return
    
    target = context.args[0]
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Check if it's a username (starts with @)
        if target.startswith("@"):
            username = target[1:]  # Remove the @
            cursor.execute("UPDATE users SET subscription_status = 'suspended' WHERE telegram_username = ?", (username,))
            conn.commit()
            
            if cursor.rowcount > 0:
                await update.message.reply_text(f"✅ User @{username} has been suspended.", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"❌ User @{username} not found.", parse_mode="Markdown")
        else:
            # Check if it's a USER_ID (starts with USR_)
            if target.startswith("USR_"):
                cursor.execute("UPDATE users SET subscription_status = 'suspended' WHERE user_id = ?", (target,))
                conn.commit()
                
                if cursor.rowcount > 0:
                    await update.message.reply_text(f"✅ User `{target}` has been suspended.", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ User `{target}` not found.", parse_mode="Markdown")
            else:
                # It's a KEY_CODE
                cursor.execute("""
                    SELECT user_id FROM users WHERE key_code = ?
                """, (target,))
                user_result = cursor.fetchone()
                
                if user_result:
                    cursor.execute("UPDATE users SET subscription_status = 'suspended' WHERE key_code = ?", (target,))
                    conn.commit()
                    await update.message.reply_text(f"✅ User with key `{target}` has been suspended.", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ Key `{target}` not found.", parse_mode="Markdown")
        
        conn.close()
    except Exception as e:
        logger.error(f"Error suspending user: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def unsuspend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsuspend a user (admin only)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized. Only admin can unsuspend users.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: `/unsuspend KEY_CODE` or `/unsuspend @username` or `/unsuspend USER_ID`", parse_mode="Markdown")
        return
    
    target = context.args[0]
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Check if it's a username (starts with @)
        if target.startswith("@"):
            username = target[1:]  # Remove the @
            cursor.execute("UPDATE users SET subscription_status = 'active' WHERE telegram_username = ?", (username,))
            conn.commit()
            
            if cursor.rowcount > 0:
                await update.message.reply_text(f"✅ User @{username} has been unsuspended.", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"❌ User @{username} not found.", parse_mode="Markdown")
        else:
            # Check if it's a USER_ID (starts with USR_)
            if target.startswith("USR_"):
                cursor.execute("UPDATE users SET subscription_status = 'active' WHERE user_id = ?", (target,))
                conn.commit()
                
                if cursor.rowcount > 0:
                    await update.message.reply_text(f"✅ User `{target}` has been unsuspended.", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ User `{target}` not found.", parse_mode="Markdown")
            else:
                # It's a KEY_CODE
                cursor.execute("""
                    SELECT user_id FROM users WHERE key_code = ?
                """, (target,))
                user_result = cursor.fetchone()
                
                if user_result:
                    cursor.execute("UPDATE users SET subscription_status = 'active' WHERE key_code = ?", (target,))
                    conn.commit()
                    await update.message.reply_text(f"✅ User with key `{target}` has been unsuspended.", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ Key `{target}` not found.", parse_mode="Markdown")
        
        conn.close()
    except Exception as e:
        logger.error(f"Error unsuspending user: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def call_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Make a call (requires active subscription)"""
    user_id = update.effective_user.id
    
    # Check subscription
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT subscription_status, expires_at, key_code FROM users WHERE telegram_id = ? ORDER BY created_at DESC LIMIT 1
        """, (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data:
            conn.close()
            await update.message.reply_text("❌ No active subscription. Use `/redeem KEY_CODE` to activate.", parse_mode="Markdown")
            return
        
        status, expires_at, key_code = user_data
        
        # Check if user is suspended
        if status == "suspended":
            conn.close()
            await update.message.reply_text("❌ Your subscription has been suspended. You cannot make calls.")
            return
        
        # Check if subscription is revoked
        if status == "revoked":
            conn.close()
            await update.message.reply_text("❌ Your subscription has been revoked.")
            return
        
        # Check if key is revoked
        cursor.execute("SELECT status FROM keys WHERE key_code = ?", (key_code,))
        key_data = cursor.fetchone()
        
        if key_data and key_data[0] == "revoked":
            conn.close()
            await update.message.reply_text("❌ Your key has been revoked. You cannot make calls.")
            return
        
        # Check if subscription is expired
        expires_dt = datetime.fromisoformat(expires_at)
        if datetime.now() > expires_dt:
            conn.close()
            await update.message.reply_text("❌ Your subscription has expired. Use `/redeem KEY_CODE` to activate a new one.", parse_mode="Markdown")
            return
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return
    
    # Parse call parameters
    if not context.args or len(context.args) < 4:
        await update.message.reply_text(
            "Usage: `/call <customer_number> <your_number> <customer_name> <service_name>`\n\n"
            "Example: `/call +16315127338 +12566967661 John Smith PayPal`",
            parse_mode="Markdown"
        )
        return
    
    customer_number = context.args[0]
    our_number = context.args[1]
    customer_name = context.args[2]
    service_name = " ".join(context.args[3:])
    
    try:
        await update.message.reply_text("📞 Initiating call...")
        
        # Send notification to admin
        admin_msg = f"""
📞 **Call Initiated**

From: {our_number}
To: {customer_number}
Customer: {customer_name}
Service: {service_name}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        try:
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=admin_msg, parse_mode="Markdown")
        except:
            pass
        
        # Make the call with Twilio
        call = twilio_client.calls.create(
            to=customer_number,
            from_=our_number,
            url=f"{RENDER_URL}/voice"
        )
        
        await update.message.reply_text(f"✅ Call initiated successfully!\nCall SID: `{call.sid}`", parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error initiating call: {e}")
        error_msg = f"❌ Error initiating call:\n\n{str(e)}"
        await update.message.reply_text(error_msg)
        
        # Notify admin of error
        try:
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"❌ Call Error: {str(e)}",
                parse_mode="Markdown"
            )
        except:
            pass

async def main():
    """Start the bot"""
    # Initialize database
    init_database()
    
    # Create application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("genkey", genkey))
    app.add_handler(CommandHandler("redeem", redeem))
    app.add_handler(CommandHandler("subscription", subscription))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("suspend", suspend))
    app.add_handler(CommandHandler("unsuspend", unsuspend))
    app.add_handler(CommandHandler("call", call_command))
    
    # Start polling
    logger.info("Starting Telegram bot...")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
