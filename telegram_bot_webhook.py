# telegram_bot_webhook.py
import os
import ssl
import httpx
import requests, subprocess
import json
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.constants import ParseMode

# New imports for webhook
import logging
from expenses_sqlite import ExpensesSQLite
import pandas as pd
from s3_storage import S3Storage, backup_db_to_s3, restore_db_from_s3
import time
import atexit
import threading
import asyncio
import prompts
# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
# Set more verbose logging for debugging
logging.getLogger('telegram').setLevel(logging.DEBUG)
logging.getLogger('httpx').setLevel(logging.INFO)

# Monkey patch httpx AsyncClient to disable SSL verification
original_init = httpx.AsyncClient.__init__

def patched_init(self, *args, **kwargs):
    kwargs['verify'] = False
    return original_init(self, *args, **kwargs)

httpx.AsyncClient.__init__ = patched_init

BOT_TOKEN = os.getenv("TELE_API_KEY")  # Replace with your bot token
MODEL = "qwen/qwen3-32b"
# Webhook settings
PORT = int(os.environ.get("PORT", 8443))
# Update the WEBHOOK_URL to use the primary URL from Render
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://my-financier.onrender.com")

from groq import Groq, AsyncGroq
client = AsyncGroq(
    api_key=os.environ.get("GROQ_API_KEY"),
    http_client=httpx.AsyncClient(verify=False)
)
# from openai import AsyncOpenAI

# client = AsyncOpenAI(
#     base_url = 'http://localhost:11434/v1',
#     api_key='ollama', # required, but unused
# )

# Initialize the expenses database with S3 backup/restore
db_path = os.environ.get("DATABASE_PATH", "expenses.db")
logger.info("🔷🔷🔷 Using database path: %s", db_path)

# Check if we should restore from S3 first (on startup)
if os.environ.get("S3_ENABLED", "false").lower() == "true":
    logger.info("🔷🔷🔷 S3 storage is enabled")
    
    # Try to restore from S3 first (if available)
    logger.info("🔷🔷🔷 Attempting to restore database from S3...")
    restored = restore_db_from_s3(db_path)
    if restored:
        logger.info("✅✅✅ Successfully restored database from S3")
    else:
        logger.warning("⚠️⚠️⚠️ Could not restore from S3, using local database")

db = ExpensesSQLite(db_path)

# Set up scheduled backups with 15-minute interval
BACKUP_INTERVAL = int(os.environ.get("BACKUP_INTERVAL_SECONDS", 900))  # Default: 15 minutes (900 seconds)

# Global backup scheduler variables
backup_scheduler = None
backup_lock = threading.Lock()
pending_backup = False

# Define IST timezone (GMT+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

def get_current_time_ist() -> datetime:
    """Get current time in IST (GMT+5:30)"""
    return datetime.now(IST)

def should_backup() -> bool:
    """Check if it's time to backup based on database-stored timestamp in IST"""
    last_backup = db.get_last_backup_time()
    if not last_backup:
        return True
    
    current_time_ist = get_current_time_ist()
    time_since_backup = current_time_ist - last_backup
    return time_since_backup.total_seconds() >= BACKUP_INTERVAL

def perform_backup_sync():
    """Synchronous backup function for background thread"""
    global pending_backup
    
    with backup_lock:
        if not pending_backup and not should_backup():
            return
        
        pending_backup = False
        logger.info("🔷🔷🔷 Running scheduled database backup to S3...")
        
        if os.environ.get("S3_ENABLED", "false").lower() == "true":
            success = backup_db_to_s3(db_path)
            if success:
                logger.info("✅✅✅ S3 backup successful")
            else:
                logger.error("❌❌❌ S3 backup failed")
        else:
            logger.info("🔷🔷🔷 S3 is not enabled, skipping backup")

def trigger_backup():
    """Trigger an immediate backup (called after data modifications)"""
    global pending_backup
    
    with backup_lock:
        pending_backup = True
        logger.info("🔷🔷🔷 Backup triggered due to data modification")

def backup_scheduler_thread():
    """Background thread that runs backup checks periodically"""
    logger.info("🔷🔷🔷 Starting backup scheduler thread")
    
    while True:
        try:
            perform_backup_sync()
            # Check every 60 seconds, but backup only when needed
            time.sleep(60)
        except Exception as e:
            logger.error("❌❌❌ Error in backup scheduler: %s", str(e))
            time.sleep(60)

def start_backup_scheduler():
    """Start the background backup scheduler"""
    global backup_scheduler
    
    if backup_scheduler is None or not backup_scheduler.is_alive():
        backup_scheduler = threading.Thread(target=backup_scheduler_thread, daemon=True)
        backup_scheduler.start()
        logger.info("✅✅✅ Backup scheduler started")

def stop_backup_scheduler():
    """Stop the background backup scheduler"""
    global backup_scheduler
    
    if backup_scheduler and backup_scheduler.is_alive():
        logger.info("🔷🔷🔷 Stopping backup scheduler...")
        # Skip final backup on exit

# Register backup function on exit
def exit_handler():
    """Clean shutdown without backup"""
    stop_backup_scheduler()
    logger.info("🔷🔷🔷 Clean shutdown completed")

# Log process restart detection and start backup scheduler
startup_time = get_current_time_ist()
last_backup = db.get_last_backup_time()
if last_backup:
    time_since_last_backup = startup_time - last_backup
    logger.info("🔷🔷🔷 Process restarted. Last backup was %d minutes ago", 
               time_since_last_backup.total_seconds() // 60)
    
    # If it's been too long since last backup, trigger one immediately
    if time_since_last_backup.total_seconds() > BACKUP_INTERVAL:
        logger.info("🔷🔷🔷 Triggering immediate backup due to process restart")
        trigger_backup()
else:
    logger.info("🔷🔷🔷 No previous backup found, will backup on first activity")
    trigger_backup()

# Start the background backup scheduler
start_backup_scheduler()

# Normalize existing data on startup
db.normalize_existing_data()

# Define tools for OpenAI API
tools = [
    {
        "type": "function",
        "function": {
            "name": "add_expense",
            "description": "Add a new expense to the database",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "The amount of the expense"},
                    "category": {"type": "string", "description": "Category: Food, Transportation, Utilities, Entertainment, Healthcare, Education, Shopping, Travel, Dining, Groceries, Rent, Gifts, Donations, Subscriptions, Personal Care, Miscellaneous"},
                    "kakeibo_category": {"type": "string", "description": "Kakeibo category: survival (needs), optional (wants), culture (self-improvement), extra (unexpected)"},
                    "description": {"type": "string", "description": "Description of the expense"},
                    "user_id": {"type": "string", "description": "User ID (optional)"}
                },
                "required": ["amount", "category", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_monthly_expenses",
            "description": "Get all expenses for a specific month",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "Year (optional, defaults to current)"},
                    "month": {"type": "integer", "description": "Month (optional, defaults to current)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_category_summary",
            "description": "Get spending summary by category",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD, optional)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD, optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_expenses",
            "description": "Get recent expenses",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of days to look back (default: 7)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_expense_by_category",
            "description": "Get expenses filtered by category",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Category name"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD, optional)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD, optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_kakeibo_summary",
            "description": "Get spending summary by kakeibo categories",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD, optional)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD, optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_kakeibo_balance_analysis",
            "description": "Analyze kakeibo balance and provide recommendations",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD, optional)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD, optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_expenses",
            "description": "Get top expenses by amount",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of top expenses to return (default: 10)"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD, optional)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD, optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_spending_trends",
            "description": "Get monthly spending trends",
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {"type": "integer", "description": "Number of months to analyze (default: 6)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "normalize_categories",
            "description": "Normalize all existing categories to handle case sensitivity",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_expense",
            "description": "Find and edit an existing expense. Can search by description, amount, category, or date to find the exact expense to modify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_description": {"type": "string", "description": "Part of the description to search for the expense to edit"},
                    "search_amount": {"type": "number", "description": "Amount to search for the expense to edit"},
                    "search_category": {"type": "string", "description": "Category to search for the expense to edit"},
                    "search_date": {"type": "string", "description": "Date (YYYY-MM-DD) to search for the expense to edit"},
                    "new_amount": {"type": "number", "description": "New amount for the expense (optional)"},
                    "new_category": {"type": "string", "description": "New category for the expense (optional)"},
                    "new_kakeibo_category": {"type": "string", "description": "New kakeibo category (optional): survival, optional, culture, extra"},
                    "new_description": {"type": "string", "description": "New description for the expense (optional)"},
                    "new_date": {"type": "string", "description": "New date (YYYY-MM-DD) for the expense (optional)"},
                    "expense_index": {"type": "integer", "description": "If multiple expenses found, specify which one to edit (1-based index)"}
                },
                "required": []
            }
        }
    }
]

async def execute_tool(tool_name: str, arguments: dict, user_id: str = None) -> str:
    """Execute the requested tool function"""
    try:
        # Ensure user exists in database
        if user_id and not db.get_user(user_id):
            db.create_user(user_id)
        
        # Track if this is a data modification operation
        is_modification = False
        
        if tool_name == "add_expense":
            result = db.add_expense(
                amount=arguments.get("amount"),
                category=arguments.get("category"),
                kakeibo_category=arguments.get("kakeibo_category", "survival"),
                description=arguments.get("description"),
                user_id=user_id or "telegram_user"
            )
            is_modification = True
            response = f"✅ Expense added: ₹{result['amount']} for {result['category']} ({result['kakeibo_category']}) - {result['description']}"
        
        elif tool_name == "normalize_categories":
            db.normalize_existing_data()
            is_modification = True
            response = "✅ All categories have been normalized to handle case sensitivity"
        
        elif tool_name == "get_monthly_expenses":
            df = db.get_monthly_expenses(
                year=arguments.get("year"),
                month=arguments.get("month"),
                user_id=user_id
            )
            if df.empty:
                return "No expenses found for the specified month."
            
            total = df['amount'].sum()
            count = len(df)
            result = f"📊 Monthly Expenses:\nTotal: ₹{total:.2f}\nTransactions: {count}\n\n"
            
            # Top categories
            category_totals = df.groupby('category')['amount'].sum().sort_values(ascending=False).head(5)
            result += "Top Categories:\n"
            for cat, amount in category_totals.items():
                result += f"• {cat}: ₹{amount:.2f}\n"
            response = result
        
        elif tool_name == "get_category_summary":
            summary = db.get_category_summary(
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                user_id=user_id
            )
            if not summary:
                return "No expenses found for category analysis."
            
            result = "📊 Category Summary:\n"
            total_amount = sum(data['total'] for data in summary.values())
            result += f"Total: ₹{total_amount:.2f}\n\n"
            
            for category, data in sorted(summary.items(), key=lambda x: x[1]['total'], reverse=True):
                percentage = (data['total'] / total_amount) * 100
                result += f"{category}: ₹{data['total']:.2f} ({percentage:.1f}%)\n"
            response = result
        
        elif tool_name == "get_recent_expenses":
            days = arguments.get("days", 7)
            current_time_ist = get_current_time_ist()
            end_date = current_time_ist
            start_date = end_date - timedelta(days=days)
            
            df = db.get_expenses(
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d'),
                user_id=user_id
            )
            
            if df.empty:
                return f"No expenses found in the last {days} days."
            
            total = df['amount'].sum()
            result = f"📊 Recent Expenses ({days} days):\nTotal: ₹{total:.2f}\n\n"
            
            # Latest transactions
            # latest = df.sort_values('date', ascending=False).head(5)
            latest = df.sort_values('date', ascending=False)
            for _, row in latest.iterrows():
                date_str = pd.to_datetime(row['date']).strftime('%m-%d')
                result += f"• {date_str}: ₹{row['amount']:.2f} - {row['description']} - Category: {row['category']}\n"
            response = result
        
        elif tool_name == "get_expense_by_category":
            df = db.get_expenses(
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                category=arguments.get("category"),
                user_id=user_id
            )
            
            if df.empty:
                return f"No expenses found for category '{arguments.get('category')}'."
            
            total = df['amount'].sum()
            count = len(df)
            result = f"📊 {arguments.get('category')} Expenses:\nTotal: ₹{total:.2f}\nTransactions: {count}\n\n"
            
            # Recent transactions
            recent = df.sort_values('date', ascending=False).head(5)
            recent = df.sort_values('date', ascending=False)
            for _, row in recent.iterrows():
                date_str = pd.to_datetime(row['date']).strftime('%m-%d')
                result += f"• {date_str}: ₹{row['amount']:.2f} - {row['description']}\n"
            response = result
        
        elif tool_name == "get_kakeibo_summary":
            summary = db.get_kakeibo_summary(
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                user_id=user_id
            )
            if not summary:
                return "No expenses found for kakeibo analysis."
            
            result = "🏮 Kakeibo Summary:\n"
            total_amount = sum(data['total'] for data in summary.values())
            result += f"Total: ₹{total_amount:.2f}\n\n"
            
            kakeibo_order = ['survival', 'optional', 'culture', 'extra']
            for category in kakeibo_order:
                if category in summary:
                    data = summary[category]
                    percentage = (data['total'] / total_amount) * 100
                    emoji = {'survival': '🏠', 'optional': '🛍️', 'culture': '📚', 'extra': '⚡'}
                    result += f"{emoji.get(category, '💰')} {category.title()}: ₹{data['total']:.2f} ({percentage:.1f}%)\n"
            
            response = result
        
        elif tool_name == "get_kakeibo_balance_analysis":
            analysis = db.get_kakeibo_balance_analysis(
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                user_id=user_id
            )
            if not analysis:
                return "No expenses found for kakeibo balance analysis."
            
            result = "⚖️ Kakeibo Balance Analysis:\n\n"
            
            for category, data in analysis.items():
                emoji = {'survival': '🏠', 'optional': '🛍️', 'culture': '📚', 'extra': '⚡'}
                status_emoji = '🔴' if data['status'] == 'over' else '🟢'
                
                result += f"{emoji.get(category, '💰')} {category.title()}:\n"
                result += f"  Actual: {data['actual_percentage']:.1f}% (₹{data['actual_amount']:.2f})\n"
                result += f"  Recommended: {data['recommended_percentage']:.1f}%\n"
                result += f"  Status: {status_emoji} {data['variance']:+.1f}%\n\n"
            
            response = result
        
        elif tool_name == "get_top_expenses":
            df = db.get_top_expenses(
                limit=arguments.get("limit", 10),
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                user_id=user_id
            )
            
            if df.empty:
                return "No expenses found."
            
            result = f"💸 Top {len(df)} Expenses:\n\n"
            for _, row in df.iterrows():
                date_str = pd.to_datetime(row['date']).strftime('%m-%d')
                result += f"• {date_str}: ₹{row['amount']:.2f} - {row['category']} ({row['description']})\n"
            
            response = result
        
        elif tool_name == "get_spending_trends":
            trends = db.get_spending_trends(
                months=arguments.get("months", 6),
                user_id=user_id
            )
            
            if not trends:
                return "No spending trends data available."
            
            result = "📈 Spending Trends:\n\n"
            for month, data in sorted(trends.items()):
                result += f"📅 {month}: ₹{data['total']:.2f} ({data['transactions']} transactions)\n"
            
            # Calculate trend direction
            trend_values = [data['total'] for data in trends.values()]
            if len(trend_values) >= 2:
                recent_avg = sum(trend_values[:2]) / 2
                older_avg = sum(trend_values[-2:]) / 2
                trend_direction = "📈 Increasing" if recent_avg > older_avg else "📉 Decreasing"
                result += f"\nTrend: {trend_direction}"
            
            response = result
        
        elif tool_name == "edit_expense":
            # Search for expenses matching the criteria
            search_criteria = {}
            if arguments.get("search_description"):
                search_criteria["description"] = arguments["search_description"]
            if arguments.get("search_amount"):
                search_criteria["amount"] = arguments["search_amount"]
            if arguments.get("search_category"):
                search_criteria["category"] = arguments["search_category"]
            if arguments.get("search_date"):
                search_criteria["date"] = arguments["search_date"]
            
            # Add user_id to search criteria
            search_criteria["user_id"] = user_id
            
            # Find matching expenses
            matching_expenses = db.find_expenses_by_criteria(**search_criteria, limit=5)
            
            if matching_expenses.empty:
                return "❌ No expenses found matching your search criteria. Please provide more specific details like description, amount, category, or date."
            
            # If multiple expenses found, let user choose or use index
            expense_index = arguments.get("expense_index", 1) - 1  # Convert to 0-based
            
            if len(matching_expenses) > 1 and expense_index >= len(matching_expenses):
                response = f"🔍 Found {len(matching_expenses)} matching expenses:\n\n"
                for i, (_, expense) in enumerate(matching_expenses.iterrows(), 1):
                    date_str = expense['date'].strftime('%Y-%m-%d')
                    response += f"{i}. {date_str}: ₹{expense['amount']:.2f} - {expense['category']} - {expense['description']}\n"
                response += f"\nPlease specify which expense to edit by saying 'edit expense number X' where X is the number (1-{len(matching_expenses)})."
                return response
            
            if expense_index >= len(matching_expenses) or expense_index < 0:
                expense_index = 0  # Default to first match
            
            # Get the expense to edit
            expense_to_edit = matching_expenses.iloc[expense_index]
            expense_id = expense_to_edit['id']
            
            # Prepare update parameters
            update_params = {}
            if arguments.get("new_amount") is not None:
                update_params["amount"] = arguments["new_amount"]
            if arguments.get("new_category"):
                update_params["category"] = arguments["new_category"]
            if arguments.get("new_kakeibo_category"):
                update_params["kakeibo_category"] = arguments["new_kakeibo_category"]
            if arguments.get("new_description"):
                update_params["description"] = arguments["new_description"]
            if arguments.get("new_date"):
                update_params["date"] = arguments["new_date"]
            
            if not update_params:
                return "❌ No new values provided for updating. Please specify what you want to change (amount, category, description, etc.)."
            
            # Update the expense
            success = db.update_expense(expense_id, **update_params)
            
            if success:
                is_modification = True
                
                # Show before and after
                response = "✅ Expense updated successfully!\n\n"
                response += f"📅 Original: {expense_to_edit['date'].strftime('%Y-%m-%d')}: ₹{expense_to_edit['amount']:.2f} - {expense_to_edit['category']} - {expense_to_edit['description']}\n\n"
                
                # Show what was changed
                changes = []
                if "amount" in update_params:
                    changes.append(f"Amount: ₹{expense_to_edit['amount']:.2f} → ₹{update_params['amount']:.2f}")
                if "category" in update_params:
                    changes.append(f"Category: {expense_to_edit['category']} → {update_params['category']}")
                if "kakeibo_category" in update_params:
                    changes.append(f"Kakeibo: {expense_to_edit['kakeibo_category']} → {update_params['kakeibo_category']}")
                if "description" in update_params:
                    changes.append(f"Description: {expense_to_edit['description']} → {update_params['description']}")
                if "date" in update_params:
                    changes.append(f"Date: {expense_to_edit['date'].strftime('%Y-%m-%d')} → {update_params['date']}")
                
                response += "🔄 Changes made:\n" + "\n".join(f"   • {change}" for change in changes)
                return response
            else:
                return "❌ Failed to update expense. Please try again."
        
        else:
            return f"Unknown tool: {tool_name}"
        
        # Trigger backup if this was a data modification
        if is_modification:
            trigger_backup()
        
        return response
    
    except Exception as e:
        return f"Error executing {tool_name}: {str(e)}"

async def call_openai_api(prompt: str, user_id: str = None, model: str = "llama3.2") -> str:
    """Call OpenAI API with tools and return the response"""
    try:
        system = prompts.get_system_prompt()

        logger.info("🔷🔷🔷 INSTRUCTION: %s 🔷🔷🔷", prompt)
        
        response = await client.chat.completions.create(
            # model="qwen3",
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ],
            tools=tools,
            tool_choice="auto",
            max_tokens=1000,
            temperature=0.7
        )
        
        # Check if the model wants to call a function
        if response.choices[0].message.tool_calls:
            tool_results = []
            generate_report = True
            for tool_call in response.choices[0].message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                tool_args = function_args.copy()
                # Execute the tool
                if function_name == "add_expense":
                    generate_report = False
                
                logger.info("🛠️🛠️🛠️ Executing tool: %s with args: %s", function_name, function_args)
                tool_result = await execute_tool(function_name, tool_args, user_id)
                tool_results.append(tool_result)
            if generate_report:
                
                return "\n".join(tool_results)
                system_msg_reports = """You are a helpful assistant that summarizes the results of the executed tools. Generate a concise report based on the tool outputs.

                1. You are returning a summary to a telegram bot
                2. Generate charts or tables if relevant
                3. Generate in html format
                4. Unsupported tags in telegram: <div>, <p>, <br>, <img>, <ul> along with below tags

                | Tag                                         | Description     |
                | ------------------------------------------- | --------------- |
                | `<div>`                                     | Block container |
                | `<p>`                                       | Paragraph       |
                | `<br>`                                      | Line break      |
                | `<img>`                                     | Image tag       |
                | `<ul>`, `<ol>`, `<li>`                      | Lists           |
                | `<h1>` to `<h6>`                            | Headings        |
                | `<table>`, `<tr>`, `<td>`                   | Tables          |
                | `<span>` *(without class="tg-spoiler")*     | Inline styling  |
                | `<blockquote>`                              | Quotes          |
                | `<hr>`                                      | Horizontal rule |
                | `<input>`, `<form>`, `<button>`             | Forms           |
                | `<style>`, `<script>`                       | Scripts & CSS   |
                | `<iframe>`, `<embed>`, `<video>`, `<audio>` | Media embeds    |
                | `<meta>`, `<link>`                          | Head content    |

                5. You can use the following tags in your response:

                | You want...       | Use this instead...                                          |
                | ----------------- | ------------------------------------------------------------ |
                | Line breaks       | `\n` instead of `<br>` or `<p>`                              |
                | Headings (`<h1>`) | Use bold: `<b>Heading</b>`                                   |
                | Lists             | Manually format with bullets: `• Item 1\n• Item 2`           |
                | Tables            | Use monospace + spacing: `<pre>Col1  Col2\nVal1  Val2</pre>` |
                | Divs for layout   | Avoid — Telegram doesn’t support layout HTML                 |

                
                /no_think"""
                followup_response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg_reports},
                        {"role": "user", "content": "User asks for: "+prompt+"\nHere are the results of the executed tools:\n" + "\n".join(tool_results)}
                    ],
                    max_tokens=500,
                    temperature=0.7
                )
                return followup_response.choices[0].message.content.replace("<think>", "").replace("</think>", "").strip()

            else:
                return "\n".join(tool_results)
        else:
            return response.choices[0].message.content
        
    except Exception as e:
        logger.error("❌❌❌ Error calling OpenAI API: %s", e)
        return f"Error calling OpenAI API: {e}"

async def be_alive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /be_alive command to check if bot is running - silent response"""
    # Just log that the command was received, don't send any response
    logger.info("🔄 Received /be_alive command from user %s", 
               update.message.from_user.id if update.message.from_user else "unknown")
    # No await or return needed - just process silently

async def alive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /alive command to check if bot is running"""
    current_time_ist = get_current_time_ist()
    uptime = (current_time_ist - startup_time).total_seconds() // 60
    response = f"🤖 Bot is alive! Uptime: {uptime:.0f} minutes\nCurrent time (IST): {current_time_ist.strftime('%Y-%m-%d %H:%M:%S')}"
    await update.message.reply_text(response)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 Welcome to your Kakeibo Finance Assistant!

I can help you:
💰 Track expenses: "Spent 500 on groceries" 
📊 Monthly summary: "Show this month's expenses"
🏮 Kakeibo analysis: "Show kakeibo summary"
⚖️ Balance check: "Analyze my kakeibo balance"
🏷️ Category analysis: "Show category summary"
📈 Spending trends: "Show spending trends"
💸 Top expenses: "Show my top expenses"
🔍 Recent expenses: "Show recent expenses"

Kakeibo Categories:
🏠 Survival - Basic needs
🛍️ Optional - Wants & desires  
📚 Culture - Self-improvement
⚡ Extra - Unexpected expenses

Admin Commands (for authorized users):
🔧 /backup - Manual backup
🧹 /cleanup - Clean old backups
📊 /status - System status

Just tell me what you need!
"""
    await update.message.reply_text(help_text)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /backup command for admin users"""
    username = update.message.from_user.username or f"user_{update.message.from_user.id}"
    
    # Check if user is admin
    if username != os.environ.get("ADMIN_USERNAME"):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    if os.environ.get("S3_ENABLED", "false").lower() == "true":
        await update.message.reply_text("🔄 Starting manual database backup...")
        trigger_backup()  # Trigger immediate backup
        
        # Wait a moment for backup to complete
        await asyncio.sleep(3)
        
        # Show backup status with timestamp
        last_backup = db.get_last_backup_time()
        status_msg = "✅ Manual backup triggered"
        if last_backup:
            status_msg += f"\n📅 Last backup: {last_backup.strftime('%Y-%m-%d %H:%M:%S')}"
        await update.message.reply_text(status_msg)
    else:
        await update.message.reply_text("❌ S3 backup is not enabled")

async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cleanup command for admin users"""
    username = update.message.from_user.username or f"user_{update.message.from_user.id}"
    
    # Check if user is admin
    if username != os.environ.get("ADMIN_USERNAME"):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    if os.environ.get("S3_ENABLED", "false").lower() == "true":
        await update.message.reply_text("🧹 Starting backup cleanup...")
        try:
            s3 = S3Storage()
            s3.cleanup_old_backups()
            db.set_setting('last_cleanup_time', datetime.now().isoformat())
            await update.message.reply_text("✅ Cleanup completed successfully")
        except Exception as e:
            logger.error("❌❌❌ Cleanup failed: %s", str(e))
            await update.message.reply_text(f"❌ Cleanup failed: {str(e)}")
    else:
        await update.message.reply_text("❌ S3 backup is not enabled")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command for admin users"""
    username = update.message.from_user.username or f"user_{update.message.from_user.id}"
    
    # Check if user is admin
    if username != os.environ.get("ADMIN_USERNAME"):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    last_backup = db.get_last_backup_time()
    last_cleanup = db.get_setting('last_cleanup_time')
    current_time_ist = get_current_time_ist()
    
    status_msg = f"🤖 **System Status Report** (IST)\n\n"
    status_msg += f"📅 **Process Info:**\n"
    status_msg += f"   • Started: {startup_time.strftime('%Y-%m-%d %H:%M:%S IST')}\n"
    status_msg += f"   • Current time: {current_time_ist.strftime('%Y-%m-%d %H:%M:%S IST')}\n"
    status_msg += f"   • Uptime: {((current_time_ist - startup_time).total_seconds() // 60):.0f} minutes\n\n"
    
    status_msg += f"💾 **Backup Status:**\n"
    if last_backup:
        minutes_ago = (current_time_ist - last_backup).total_seconds() // 60
        status_msg += f"   • Last backup: {last_backup.strftime('%Y-%m-%d %H:%M:%S IST')}\n"
        status_msg += f"   • Time ago: {int(minutes_ago)} minutes\n"
    else:
        status_msg += "   • Last backup: Never\n"
    
    status_msg += f"🧹 **Cleanup Status:**\n"
    if last_cleanup:
        cleanup_time = datetime.fromisoformat(last_cleanup.replace('Z', '+00:00'))
        if cleanup_time.tzinfo is None:
            cleanup_time = cleanup_time.replace(tzinfo=timezone.utc)
        cleanup_time_ist = cleanup_time.astimezone(IST)
        minutes_ago = (current_time_ist - cleanup_time_ist).total_seconds() // 60
        status_msg += f"   • Last cleanup: {cleanup_time_ist.strftime('%Y-%m-%d %H:%M:%S IST')}\n"
        status_msg += f"   • Time ago: {int(minutes_ago)} minutes\n"
    else:
        status_msg += "   • Last cleanup: Never\n"
    
    # Show backup scheduler status
    scheduler_status = "🟢 Running" if backup_scheduler and backup_scheduler.is_alive() else "🔴 Stopped"
    status_msg += f"\n🔄 **Background Services:**\n"
    status_msg += f"   • Backup scheduler: {scheduler_status}\n"
    status_msg += f"   • Backup interval: {BACKUP_INTERVAL // 60} minutes\n"
    
    # Show S3 configuration
    if os.environ.get("S3_ENABLED", "false").lower() == "true":
        status_msg += f"\n☁️ **S3 Configuration:**\n"
        status_msg += f"   • Bucket: {os.environ.get('S3_BUCKET', 'Not set')}\n"
        status_msg += f"   • Max backups: {os.environ.get('S3_MAX_BACKUPS', '96')}\n"
        status_msg += f"   • Max age: {os.environ.get('S3_MAX_AGE_DAYS', '7')} days\n"
    else:
        status_msg += f"\n☁️ **S3 Configuration:** ❌ Disabled\n"
    
    await update.message.reply_text(status_msg)

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logs command for admin users to see recent activity"""
    username = update.message.from_user.username or f"user_{update.message.from_user.id}"
    
    # Check if user is admin
    if username != os.environ.get("ADMIN_USERNAME"):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    try:
        # Get recent expenses count and last activity (last 24h in IST)
        current_time_ist = get_current_time_ist()
        start_time_ist = current_time_ist - timedelta(hours=24)
        
        recent_df = db.get_expenses(
            start_date=start_time_ist.strftime('%Y-%m-%d'),
            end_date=current_time_ist.strftime('%Y-%m-%d')
        )
        
        # Get user count
        users = db.list_users()
        
        logs_msg = f"📊 **Activity Report (Last 24h IST)**\n\n"
        logs_msg += f"👥 **Users:** {len(users)} total\n"
        logs_msg += f"💰 **Transactions:** {len(recent_df)} in last 24h\n"
        
        if not recent_df.empty:
            total_amount = recent_df['amount'].sum()
            logs_msg += f"💵 **Total amount:** ₹{total_amount:.2f}\n"
            
            # Top categories in last 24h
            if len(recent_df) > 0:
                top_categories = recent_df.groupby('category')['amount'].sum().sort_values(ascending=False).head(3)
                logs_msg += f"\n🏷️ **Top Categories:**\n"
                for cat, amount in top_categories.items():
                    logs_msg += f"   • {cat}: ₹{amount:.2f}\n"
        
        logs_msg += f"\n🕒 **Current Time:** {current_time_ist.strftime('%Y-%m-%d %H:%M:%S IST')}"
        
        await update.message.reply_text(logs_msg)
        
    except Exception as e:
        logger.error("❌❌❌ Error generating logs: %s", str(e))
        await update.message.reply_text(f"❌ Error generating logs: {str(e)}")

async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instruction = update.message.text
    # Convert message date to IST
    message_time_utc = update.message.date
    message_time_ist = message_time_utc.astimezone(IST)
    date = message_time_ist.strftime("%Y-%m-%d")
    
    username = update.message.from_user.username or f"user_{update.message.from_user.id}"
    user_id = str(update.message.from_user.id)
    
    # Ensure user exists in database
    if not db.get_user(user_id):
        db.create_user(user_id, f"{username}@telegram.com" if username else None)
    
    instruction = f"{instruction}. Today's date is {date} (IST). User: {username}"
    try:
        # Call OpenAI API with the user's message and user_id
        response = await call_openai_api(instruction, user_id, model=MODEL)
        
        # Apply logging decorator to reply_text
        reply_func = log_response_decorator(update.message.reply_text)
        
        if "<" in response and ">" in response:
            print(response)
            response = response.replace("<think>", "").replace("</think>", "").strip()
            ### remove enclosing ```html``` tags if present
            if response.startswith("```html"):
                response = response[7:].strip()
            if response.endswith("```"):
                response = response[:-3].strip()
            # Send as HTML message
            await reply_func(response, parse_mode=ParseMode.HTML)
        else:
            await reply_func(response)
    except Exception as e:
        reply_func = log_response_decorator(update.message.reply_text)
        await reply_func(f"Error: {e}")

# Override the reply_text method to log responses
import functools

def log_response_decorator(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            # Log the outgoing response
            text = kwargs.get('text', args[0] if args else 'no_text')
            parse_mode = kwargs.get('parse_mode', 'None')
            logger.info("📤 HTTP RESPONSE: text=%s, parse_mode=%s", 
                       text[:200] + "..." if len(str(text)) > 200 else text, 
                       parse_mode)
            
            # Call the original function
            result = await func(*args, **kwargs)
            logger.info("✅ Response sent successfully")
            return result
        except Exception as e:
            logger.error("❌ Error sending response: %s", e)
            raise
    return wrapper

# def start_ollama_if_not_running():
#     """Check if Ollama is running, start it if not"""
#     try:
#         # Check if Ollama is already running
#         response = requests.get("http://localhost:11434/api/tags", timeout=5.0, verify=False)
#         if response.status_code == 200:
#             logger.info("✅✅✅ Ollama is already running")
#             return True
#     except:
#         logger.warning("⚠️⚠️⚠️ Ollama not running, attempting to start...")
        
#     try:
#         # Start Ollama in the background
#         subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
#         logger.info("✅✅✅ Started Ollama service")
        
#         # Wait a moment for it to start up
#         import time
#         time.sleep(3)
#         return True
#     except Exception as e:
#         logger.error("❌❌❌ Failed to start Ollama: %s", e)
#         return False

async def webhook_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    logger.error(f"Exception while handling an update: {context.error}")
    # Log more details about the error
    import traceback
    logger.error(traceback.format_exc())

async def log_webhook_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log incoming webhook payload for debugging"""
    try:
        # Log the raw update object
        logger.info("📥 WEBHOOK PAYLOAD: %s", update.to_dict())
        
        # Log specific details if available
        if update.message:
            logger.info("📧 MESSAGE: from_user=%s, text=%s, date=%s", 
                       update.message.from_user.id if update.message.from_user else "unknown",
                       update.message.text or "no_text",
                       update.message.date)
        
        if update.callback_query:
            logger.info("🔘 CALLBACK_QUERY: from_user=%s, data=%s", 
                       update.callback_query.from_user.id if update.callback_query.from_user else "unknown",
                       update.callback_query.data)
    except Exception as e:
        logger.error("❌ Error logging webhook payload: %s", e)

def main():
    # Create the Application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("alive", alive))
    app.add_handler(CommandHandler("be_alive", be_alive))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("cleanup", cleanup_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("logs", logs_command))
    
    # Add message handler for general messages (must be last)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_command))
    
    # Add error handler
    app.add_error_handler(webhook_error_handler)
    
    # Log the webhook URL for debugging
    webhook_path = f"/{BOT_TOKEN}"
    full_webhook_url = f"{WEBHOOK_URL}{webhook_path}"
    logger.info("🚀🚀🚀 Setting webhook: %s", full_webhook_url)
    
    # Set up webhook with correct configuration
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=full_webhook_url,
        drop_pending_updates=False  # Set to False to keep pending updates
    )
    
    logger.info("✅✅✅ Webhook set on %s", full_webhook_url)

    # Log S3 configuration status
    if os.environ.get("S3_ENABLED", "false").lower() == "true":
        logger.info("🔷🔷🔷 S3 storage is enabled with bucket: %s", os.environ.get("S3_BUCKET"))
        logger.info("🔷🔷🔷 Backup interval: %s seconds (%s minutes)", 
                   BACKUP_INTERVAL, BACKUP_INTERVAL // 60)
        logger.info("🔷🔷🔷 Backup retention: max %s backups, max %s days", 
                   os.environ.get('S3_MAX_BACKUPS', '96'), 
                   os.environ.get('S3_MAX_AGE_DAYS', '7'))
        logger.info("🔷🔷🔷 Cleanup frequency: %s minutes", 
                   os.environ.get('S3_CLEANUP_FREQUENCY_MINUTES', '60'))
        logger.info("🔷🔷🔷 Background backup scheduler: Enabled")
        logger.info("🔷🔷🔷 Admin commands: /backup, /cleanup, /status, /logs")
    else:
        logger.warning("⚠️⚠️⚠️ S3 storage is disabled")

async def call_llm(messages: list):

    response = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        stream=False,
        tool_choice="auto",
        max_tokens=1000,
        temperature=0.7
    )
    return response

if __name__ == '__main__':
    logger.info("🚀🚀🚀 Starting webhook application...")
    # main()
    ## local validate prompts
    import sys
    system = prompts.get_system_prompt()
    content = sys.argv[1] if len(sys.argv) > 1 else "Test prompt"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content}
    ]
    response = asyncio.run(call_llm(messages))
    logger.info("🔷🔷🔷 INSTRUCTION: %s 🔷🔷🔷", response)
    for tool in response.choices[0].message.tool_calls:
        logger.info("🔧🔧🔧 Tool call: %s with args: %s", tool.function.name, tool.function.arguments) 

    # logger.info("📜 SYSTEM PROMPT: %s", system)
