import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List
import sqlite3
import os
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
PATIENT_NAME, PATIENT_AGE, PATIENT_PHONE, PATIENT_DIAGNOSIS, PRESCRIPTION_INPUT, CONFIRM_PRESCRIPTION = range(6)

class MedicalBot:
    def __init__(self, token: str):
        self.token = token
        self.db_path = "medical_records.db"
        self.init_database()

    def init_database(self):
        """Initialize SQLite database for storing patient and prescription data"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Patients table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                age INTEGER,
                phone TEXT,
                telegram_user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Prescriptions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER,
                diagnosis TEXT,
                admission_date DATE,
                discharge_date DATE,
                medications TEXT, -- JSON string of medications
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (patient_id) REFERENCES patients (id)
            )
        ''')

        conn.commit()
        conn.close()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        keyboard = [
            ['👤 Add New Patient', '📋 View Patients'],
            ['💊 Create Prescription', '📊 View Prescriptions'],
            ['🔍 Search Patient', '📈 Statistics']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        welcome_text = """
🏥 **Medical Management Bot**

Welcome to the Medical Bot! I can help you:

👤 Manage patient records
💊 Create and manage prescriptions
📋 View patient history
🔍 Search through records
📊 Generate reports

Choose an option from the menu below:
"""
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def add_patient_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start adding new patient"""
        await update.message.reply_text(
            "🏥 **Adding New Patient**\n\nPlease enter the patient's full name:",
            parse_mode='Markdown'
        )
        return PATIENT_NAME

    async def patient_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get patient name"""
        context.user_data['patient_name'] = update.message.text
        await update.message.reply_text(
            f"Patient Name: **{update.message.text}**\n\nPlease enter the patient's age:",
            parse_mode='Markdown'
        )
        return PATIENT_AGE

    async def patient_age(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get patient age"""
        try:
            age = int(update.message.text)
            context.user_data['patient_age'] = age
            await update.message.reply_text(
                f"Age: **{age}** years\n\nPlease enter the patient's phone number:",
                parse_mode='Markdown'
            )
            return PATIENT_PHONE
        except ValueError:
            await update.message.reply_text("Please enter a valid age (number only):")
            return PATIENT_AGE

    async def patient_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get patient phone and save to database"""
        context.user_data['patient_phone'] = update.message.text

        # Save to database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO patients (name, age, phone, telegram_user_id)
            VALUES (?, ?, ?, ?)
        ''', (
            context.user_data['patient_name'],
            context.user_data['patient_age'],
            context.user_data['patient_phone'],
            update.effective_user.id
        ))

        patient_id = cursor.lastrowid
        conn.commit()
        conn.close()

        context.user_data['current_patient_id'] = patient_id

        summary = f"""
✅ **Patient Added Successfully!**

👤 **Name:** {context.user_data['patient_name']}
🎂 **Age:** {context.user_data['patient_age']} years
📱 **Phone:** {context.user_data['patient_phone']}
🆔 **Patient ID:** {patient_id}

Would you like to create a prescription for this patient?
"""

        keyboard = [['💊 Create Prescription', '🏠 Main Menu']]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(summary, reply_markup=reply_markup, parse_mode='Markdown')
        return ConversationHandler.END

    async def create_prescription_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start creating prescription"""
        # Get recent patients for selection
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, age FROM patients 
            WHERE telegram_user_id = ? 
            ORDER BY created_at DESC LIMIT 10
        ''', (update.effective_user.id,))

        patients = cursor.fetchall()
        conn.close()

        if not patients:
            await update.message.reply_text(
                "❌ No patients found. Please add a patient first.",
                reply_markup=ReplyKeyboardMarkup([['👤 Add New Patient']], resize_keyboard=True)
            )
            return ConversationHandler.END

        keyboard = []
        for patient in patients:
            keyboard.append([f"{patient[1]} (Age: {patient[2]}) - ID: {patient[0]}"])

        keyboard.append(['🚫 Cancel'])
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            "👤 **Select Patient for Prescription:**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return PATIENT_DIAGNOSIS

    async def patient_diagnosis(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get patient selection and ask for diagnosis"""
        if update.message.text == '🚫 Cancel':
            return await self.cancel(update, context)

        try:
            # Extract patient ID from selection
            patient_text = update.message.text
            patient_id = int(patient_text.split('ID: ')[1])
            context.user_data['selected_patient_id'] = patient_id

            await update.message.reply_text(
                "🏥 **Enter Diagnosis:**\n\nExample: Acute gastritis with H. pylori infection",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode='Markdown'
            )
            return PRESCRIPTION_INPUT
        except (IndexError, ValueError):
            await update.message.reply_text("Please select a patient from the list above.")
            return PATIENT_DIAGNOSIS

    async def prescription_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get diagnosis and start prescription input"""
        context.user_data['diagnosis'] = update.message.text

        prescription_template = """
💊 **Enter Prescription Details**

**Format for each medication:**
```
Medication Name | Dosage | Quantity | Instructions
```

**Example:**
```
Doxycycline 100mg | 100mg | 28 tablets | Take 1 tablet twice daily after meals for 14 days

Metronidazole 500mg | 500mg | 42 tablets | Take 1 tablet three times daily after meals for 14 days

Esomeprazole 40mg | 40mg | 28 tablets | Take 1 tablet twice daily before meals for 14 days

Bismuth 525mg | 525mg | 56 tablets | Take 1 tablet four times daily with meals and at bedtime for 14 days
```

**H. pylori Quadruple Therapy Template:**
Send "h.pylori" for quick H. pylori prescription template.

Please enter your prescription:
"""

        await update.message.reply_text(prescription_template, parse_mode='Markdown')
        return CONFIRM_PRESCRIPTION

    async def confirm_prescription(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parse and confirm prescription"""
        prescription_text = update.message.text.lower()

        # Check for H. pylori template
        if prescription_text == 'h.pylori':
            medications = [
                {
                    "name": "Doxycycline 100mg",
                    "dosage": "100mg",
                    "quantity": "28 tablets",
                    "instructions": "Take 1 tablet twice daily (morning and evening) after meals for 14 days"
                },
                {
                    "name": "Metronidazole 500mg", 
                    "dosage": "500mg",
                    "quantity": "42 tablets",
                    "instructions": "Take 1 tablet three times daily (morning, lunch, evening) after meals for 14 days"
                },
                {
                    "name": "Esomeprazole 40mg",
                    "dosage": "40mg", 
                    "quantity": "28 tablets",
                    "instructions": "Take 1 tablet twice daily (morning and evening) before meals for 14 days"
                },
                {
                    "name": "Bismuth 525mg",
                    "dosage": "525mg",
                    "quantity": "56 tablets", 
                    "instructions": "Take 1 tablet four times daily (morning, lunch, evening, bedtime) with meals for 14 days"
                }
            ]
        else:
            # Parse custom prescription
            medications = []
            lines = update.message.text.strip().split('\n')

            for line in lines:
                if '|' in line:
                    parts = [part.strip() for part in line.split('|')]
                    if len(parts) == 4:
                        medications.append({
                            "name": parts[0],
                            "dosage": parts[1],
                            "quantity": parts[2],
                            "instructions": parts[3]
                        })

        if not medications:
            await update.message.reply_text(
                "❌ Invalid prescription format. Please follow the example format or send 'h.pylori' for template."
            )
            return CONFIRM_PRESCRIPTION

        context.user_data['medications'] = medications

        # Generate prescription preview
        today = datetime.now().strftime("%d-%m-%Y")

        # Get patient details
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT name, age FROM patients WHERE id = ?', (context.user_data['selected_patient_id'],))
        patient = cursor.fetchone()
        conn.close()

        prescription_preview = f"""
📋 **Prescription Preview**

**Patient:** {patient[0]} (Age: {patient[1]})
**Diagnosis:** {context.user_data['diagnosis']}
**Date:** {today}

**Medications:**
"""

        for i, med in enumerate(medications, 1):
            prescription_preview += f"""
**{i}. {med['name']}**
   • Dosage: {med['dosage']}
   • Quantity: {med['quantity']}
   • Instructions: {med['instructions']}
"""

        keyboard = [['✅ Confirm & Save', '❌ Cancel', '✏️ Edit']]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            prescription_preview,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

        return CONFIRM_PRESCRIPTION

    async def save_prescription(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save prescription to database"""
        if update.message.text == '✅ Confirm & Save':
            # Save to database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            today = datetime.now().date()
            medications_json = json.dumps(context.user_data['medications'])

            cursor.execute('''
                INSERT INTO prescriptions (patient_id, diagnosis, admission_date, discharge_date, medications)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                context.user_data['selected_patient_id'],
                context.user_data['diagnosis'],
                today,
                today,
                medications_json
            ))

            prescription_id = cursor.lastrowid
            conn.commit()
            conn.close()

            success_message = f"""
✅ **Prescription Saved Successfully!**

📋 **Prescription ID:** {prescription_id}
📅 **Date:** {today.strftime("%d-%m-%Y")}

The prescription has been saved to the database.

**Next Steps:**
• Print prescription for patient
• Schedule follow-up appointment
• Monitor treatment progress
"""

            keyboard = [['🏠 Main Menu', '💊 New Prescription']]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

            await update.message.reply_text(
                success_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

            return ConversationHandler.END

        elif update.message.text == '✏️ Edit':
            await update.message.reply_text(
                "✏️ Please enter the corrected prescription:",
                reply_markup=ReplyKeyboardRemove()
            )
            return CONFIRM_PRESCRIPTION

        else:  # Cancel
            return await self.cancel(update, context)

    async def view_patients(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View all patients"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, age, phone, created_at FROM patients 
            WHERE telegram_user_id = ? 
            ORDER BY created_at DESC LIMIT 20
        ''', (update.effective_user.id,))

        patients = cursor.fetchall()
        conn.close()

        if not patients:
            await update.message.reply_text("❌ No patients found.")
            return

        patients_text = "👥 **Patient List:**\n\n"

        for patient in patients:
            created_date = datetime.fromisoformat(patient[4]).strftime("%d-%m-%Y")
            patients_text += f"""
**{patient[1]}** (ID: {patient[0]})
🎂 Age: {patient[2]} years
📱 Phone: {patient[3]}
📅 Added: {created_date}
---
"""

        await update.message.reply_text(patients_text, parse_mode='Markdown')

    async def view_prescriptions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View recent prescriptions"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.id, pt.name, p.diagnosis, p.admission_date, p.medications 
            FROM prescriptions p
            JOIN patients pt ON p.patient_id = pt.id
            WHERE pt.telegram_user_id = ?
            ORDER BY p.created_at DESC LIMIT 10
        ''', (update.effective_user.id,))

        prescriptions = cursor.fetchall()
        conn.close()

        if not prescriptions:
            await update.message.reply_text("❌ No prescriptions found.")
            return

        prescriptions_text = "📋 **Recent Prescriptions:**\n\n"

        for rx in prescriptions:
            medications = json.loads(rx[4])
            med_count = len(medications)

            prescriptions_text += f"""
**Prescription #{rx[0]}**
👤 Patient: {rx[1]}
🏥 Diagnosis: {rx[2]}
📅 Date: {rx[3]}
💊 Medications: {med_count} items
---
"""

        await update.message.reply_text(prescriptions_text, parse_mode='Markdown')

    async def search_patient(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search for patients"""
        await update.message.reply_text(
            "🔍 **Patient Search**\n\nEnter patient name or phone number to search:",
            reply_markup=ReplyKeyboardRemove()
        )

    async def handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle patient search"""
        search_term = update.message.text.strip()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, age, phone FROM patients 
            WHERE (name LIKE ? OR phone LIKE ?) AND telegram_user_id = ?
        ''', (f'%{search_term}%', f'%{search_term}%', update.effective_user.id))

        results = cursor.fetchall()
        conn.close()

        if not results:
            await update.message.reply_text(f"❌ No patients found matching '{search_term}'")
            return

        results_text = f"🔍 **Search Results for '{search_term}':**\n\n"

        for patient in results:
            results_text += f"""
**{patient[1]}** (ID: {patient[0]})
🎂 Age: {patient[2]} years  
📱 Phone: {patient[3]}
---
"""

        await update.message.reply_text(results_text, parse_mode='Markdown')

    async def statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Count patients
        cursor.execute('SELECT COUNT(*) FROM patients WHERE telegram_user_id = ?', (update.effective_user.id,))
        patient_count = cursor.fetchone()[0]

        # Count prescriptions
        cursor.execute('''
            SELECT COUNT(*) FROM prescriptions p
            JOIN patients pt ON p.patient_id = pt.id
            WHERE pt.telegram_user_id = ?
        ''', (update.effective_user.id,))
        prescription_count = cursor.fetchone()[0]

        # Today's prescriptions
        today = datetime.now().date()
        cursor.execute('''
            SELECT COUNT(*) FROM prescriptions p
            JOIN patients pt ON p.patient_id = pt.id
            WHERE pt.telegram_user_id = ? AND DATE(p.created_at) = ?
        ''', (update.effective_user.id, today))
        today_prescriptions = cursor.fetchone()[0]

        conn.close()

        stats_text = f"""
📊 **Medical Bot Statistics**

👥 **Total Patients:** {patient_count}
📋 **Total Prescriptions:** {prescription_count}
📅 **Today's Prescriptions:** {today_prescriptions}

📈 **Usage Summary:**
• Average prescriptions per patient: {prescription_count/max(patient_count, 1):.1f}
• Database created: Medical records system active
"""

        await update.message.reply_text(stats_text, parse_mode='Markdown')

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel current operation"""
        keyboard = [
            ['👤 Add New Patient', '📋 View Patients'],
            ['💊 Create Prescription', '📊 View Prescriptions'],
            ['🔍 Search Patient', '📈 Statistics']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            "❌ Operation cancelled. Back to main menu.",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button presses"""
        text = update.message.text

        if text == '👤 Add New Patient':
            return await self.add_patient_start(update, context)
        elif text == '📋 View Patients':
            await self.view_patients(update, context)
        elif text == '💊 Create Prescription':
            return await self.create_prescription_start(update, context)
        elif text == '📊 View Prescriptions':
            await self.view_prescriptions(update, context)
        elif text == '🔍 Search Patient':
            await self.search_patient(update, context)
        elif text == '📈 Statistics':
            await self.statistics(update, context)
        elif text == '🏠 Main Menu':
            await self.start(update, context)

    def run(self):
        """Run the bot"""
        application = Application.builder().token(self.token).build()

        # Conversation handler for adding patients and prescriptions
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(r'^👤 Add New Patient$'), self.add_patient_start),
                MessageHandler(filters.Regex(r'^💊 Create Prescription$'), self.create_prescription_start)
            ],
            states={
                PATIENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.patient_name)],
                PATIENT_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.patient_age)],
                PATIENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.patient_phone)],
                PATIENT_DIAGNOSIS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.patient_diagnosis)],
                PRESCRIPTION_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.prescription_input)],
                CONFIRM_PRESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.save_prescription)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel)]
        )

        # Command handlers
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(conv_handler)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.button_handler))

        # Run the bot
        print("🤖 Medical Bot is starting...")
        print("Bot is ready to receive messages!")
        application.run_polling()

# Usage
if __name__ == '__main__':
    # Replace with your actual bot token from @BotFather
    BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"

    if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("❌ Please replace BOT_TOKEN with your actual Telegram bot token")
        print("📱 Get your token from @BotFather on Telegram")
    else:
        bot = MedicalBot(BOT_TOKEN)
        bot.run()