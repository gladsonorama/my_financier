# telegram_bot.py
import os
import ssl
import httpx
import requests,subprocess
import json
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from expenses_sqlite import ExpensesSQLite
import pandas as pd

# Monkey patch httpx AsyncClient to disable SSL verification
original_init = httpx.AsyncClient.__init__

def patched_init(self, *args, **kwargs):
    kwargs['verify'] = False
    return original_init(self, *args, **kwargs)

httpx.AsyncClient.__init__ = patched_init

BOT_TOKEN = os.getenv("TELE_API_KEY")  # Replace with your bot token
MODEL = "qwen/qwen3-32b"


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



# Initialize the expenses database
db = ExpensesSQLite()

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
    }
]

async def execute_tool(tool_name: str, arguments: dict, user_id: str = None) -> str:
    """Execute the requested tool function"""
    try:
        # Ensure user exists in database
        if user_id and not db.get_user(user_id):
            db.create_user(user_id)
        
        if tool_name == "add_expense":
            result = db.add_expense(
                amount=arguments.get("amount"),
                category=arguments.get("category"),
                kakeibo_category=arguments.get("kakeibo_category", "survival"),
                description=arguments.get("description"),
                user_id=user_id or "telegram_user"
            )
            return f"✅ Expense added: ₹{result['amount']} for {result['category']} ({result['kakeibo_category']}) - {result['description']}"
        
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
            return result
        
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
            return result
        
        elif tool_name == "get_recent_expenses":
            days = arguments.get("days", 7)
            end_date = datetime.now()
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
            latest = df.sort_values('date', ascending=False).head(5)
            for _, row in latest.iterrows():
                date_str = pd.to_datetime(row['date']).strftime('%m-%d')
                result += f"• {date_str}: ₹{row['amount']:.2f} - {row['category']}\n"
            return result
        
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
            for _, row in recent.iterrows():
                date_str = pd.to_datetime(row['date']).strftime('%m-%d')
                result += f"• {date_str}: ₹{row['amount']:.2f} - {row['description']}\n"
            return result
        
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
            
            return result
        
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
            
            return result
        
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
            
            return result
        
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
            
            return result
        
        elif tool_name == "normalize_categories":
            db.normalize_existing_data()
            return "✅ All categories have been normalized to handle case sensitivity"
        
        else:
            return f"Unknown tool: {tool_name}"
    
    except Exception as e:
        return f"Error executing {tool_name}: {str(e)}"

async def call_openai_api(prompt: str, user_id: str = None, model: str = "llama3.2") -> str:
    """Call OpenAI API with tools and return the response"""
    try:
        system = """
You are a helpful finance assistant with access to expense tracking tools and Kakeibo budgeting method.

For expense tracking:
- When user mentions spending money, use add_expense tool
- Extract amount, category, description, and kakeibo_category from user input
- Categories are case-insensitive and will be normalized: Food, Transportation, Utilities, Entertainment, Healthcare, Education, Shopping, Travel, Dining, Groceries, Rent, Gifts, Donations, Subscriptions, Personal Care, Miscellaneous
- Kakeibo Categories:
  * survival: Basic needs (rent, groceries, utilities, healthcare)
  * optional: Wants and desires (entertainment, dining out, shopping)
  * culture: Self-improvement (books, courses, subscriptions)
  * extra: Unexpected expenses (repairs, emergencies)

For expense analysis:
- All category analysis is case-insensitive (food, Food, FOOD are treated the same)
- Use get_monthly_expenses for monthly summaries
- Use get_category_summary for category analysis
- Use get_kakeibo_summary for kakeibo category breakdown
- Use get_kakeibo_balance_analysis for budget balance recommendations
- Use get_recent_expenses for recent spending
- Use get_top_expenses for highest expenses
- Use get_spending_trends for monthly trends
- Use get_expense_by_category for specific category analysis
- Use normalize_categories if user wants to clean up existing data

Always use the appropriate tool for user requests. Be helpful and provide clear responses.
/no_think
"""

        print(f"Instruction: {prompt}  ")
        
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
                
                # Execute the tool
                if function_name == "add_expense":
                    generate_report = False
                
                print(f"Executing tool: {function_name} with args: {function_args}")
                tool_result = await execute_tool(function_name, function_args, user_id)
                tool_results.append(tool_result)
            if generate_report:
                
                system_msg_reports = """You are a helpful assistant that summarizes the results of the executed tools. Generate a concise report based on the tool outputs.

                1. You are returning a summary to a telegram bot
                2. Generate charts or tables if relevant
                
                /no_think"""
                followup_response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg_reports},
                        {"role": "user", "content": "Here are the results of the executed tools:\n" + "\n".join(tool_results)}
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
        return f"Error calling OpenAI API: {e}"

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

Just tell me what you need!
"""
    await update.message.reply_text(help_text)

async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instruction = update.message.text
    date = update.message.date.strftime("%Y-%m-%d")
    username = update.message.from_user.username or f"user_{update.message.from_user.id}"
    user_id = str(update.message.from_user.id)
    
    # Ensure user exists in database
    if not db.get_user(user_id):
        db.create_user(user_id, f"{username}@telegram.com" if username else None)
    
    instruction = f"{instruction}. Today's date is {date}. User: {username}"
    try:
        # Call OpenAI API with the user's message and user_id
        response = await call_openai_api(instruction, user_id, model=MODEL)
        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


def start_ollama_if_not_running():
    """Check if Ollama is running, start it if not"""
    try:
        # Check if Ollama is already running
        response = requests.get("http://localhost:11434/api/tags", timeout=5.0, verify=False)
        if response.status_code == 200:
            print("Ollama is already running")
            return True
    except:
        print("Ollama not running, attempting to start...")
        
    try:
        # Start Ollama in the background
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Started Ollama service")
        
        # Wait a moment for it to start up
        import time
        time.sleep(3)
        return True
    except Exception as e:
        print(f"Failed to start Ollama: {e}")
        return False

def main():
    # Start Ollama if not running
    # start_ollama_if_not_running()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_command))
    app.run_polling(poll_interval=10, timeout=10, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
