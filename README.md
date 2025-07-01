# My Financier - Kakeibo Telegram Bot 🏮💰

A smart personal finance management Telegram bot built with the Japanese Kakeibo budgeting method and AI-powered expense tracking.

## ✨ Features

- **AI-Powered Expense Tracking**: Natural language expense entry using Groq AI
- **Kakeibo Budgeting Method**: Traditional Japanese budgeting with four categories:
  - 🏠 **Survival** (50%) - Basic needs (rent, groceries, utilities)
  - 🛍️ **Optional** (30%) - Wants and desires (entertainment, dining out)
  - 📚 **Culture** (10%) - Self-improvement (books, courses, subscriptions)
  - ⚡ **Extra** (10%) - Unexpected expenses (repairs, emergencies)
- **Multi-User Support**: SQLite database with user management
- **Smart Analytics**:
  - Monthly summaries and trends
  - Category-wise spending analysis
  - Kakeibo balance recommendations
  - Top expenses tracking
- **Telegram Integration**: Easy-to-use chat interface with rich formatting

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Groq API Key (from [Groq Console](https://console.groq.com/))

### Installation

1. **Clone the repository**

```bash
git clone https://github.com/yourusername/my_financier.git
cd my_financier
```

2. **Install dependencies**

```bash
pip install -r requirements.txt
```

3. **Set up environment variables**

```bash
cp .env.example .env
# Edit .env with your API keys
```

4. **Run the bot**

```bash
python telegram_bot.py
```

## 🔧 Configuration

Create a `.env` file with the following variables:

```env
TELE_API_KEY=your_telegram_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
```

## 💬 Usage

### Adding Expenses

Simply message the bot in natural language:

- "Spent 500 on groceries"
- "Paid 2000 for electricity bill"
- "Movie tickets cost 400"

### Viewing Reports

- "Show this month's expenses"
- "Category summary"
- "Kakeibo analysis"
- "My top expenses"
- "Recent expenses"
- "Spending trends"

### Kakeibo Balance Analysis

Get personalized recommendations:

- "Analyze my kakeibo balance" - See if your spending aligns with Kakeibo principles

## 🏗️ Architecture

```
my_financier/
├── telegram_bot.py          # Main bot application with AI integration
├── expenses_sqlite.py       # SQLite database operations
├── expenses.db             # SQLite database (auto-created)
├── requirements.txt        # Python dependencies
├── .env.example           # Environment variables template
├── .gitignore            # Git ignore rules
└── README.md             # This file
```

## 🤖 AI Integration

The bot uses **Groq AI** with function calling to:

- Parse natural language expense descriptions
- Automatically categorize expenses
- Assign appropriate Kakeibo categories
- Generate intelligent financial reports

### Supported Models

- Default: `qwen/qwen3-32b`
- Configurable in `telegram_bot.py`

## 📊 Database Schema

### Users Table

- `id` (Primary Key)
- `username` (Unique)
- `email`
- `created_at`

### Expenses Table

- `id` (Primary Key)
- `date`
- `amount`
- `category`
- `kakeibo_category`
- `description`
- `user_id` (Foreign Key)
- `created_at`

## 🎯 Kakeibo Method

The bot implements the traditional Japanese Kakeibo budgeting method:

| Category | Percentage | Description                    |
| -------- | ---------- | ------------------------------ |
| Survival | 50%        | Essential needs for living     |
| Optional | 30%        | Things you want but don't need |
| Culture  | 10%        | Self-improvement and learning  |
| Extra    | 10%        | Unexpected expenses            |

## 🛡️ Security Features

- User isolation (each Telegram user has separate data)
- Environment variable configuration
- SQLite database with proper schema
- Input validation and error handling

## 🔄 Development

### Adding New Features

1. **New Tool Functions**: Add to `tools` array and implement in `execute_tool()`
2. **Database Methods**: Extend `ExpensesSQLite` class
3. **AI Prompts**: Modify system messages in `call_openai_api()`

### Testing

```bash
# Test database operations
python expenses_sqlite.py

# Test with sample data
python -c "from expenses_sqlite import ExpensesSQLite; db = ExpensesSQLite(); db.create_user('test', 'test@example.com')"
```

## 📈 Future Enhancements

- [ ] Web dashboard interface
- [ ] Export functionality (CSV, PDF reports)
- [ ] Budget goals and alerts
- [ ] Recurring expense tracking
- [ ] Multi-currency support
- [ ] Integration with bank APIs
- [ ] Voice message support
- [ ] Charts and visualizations

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 📞 Support

- 🐛 **Bug Reports**: [GitHub Issues](https://github.com/yourusername/my_financier/issues)
- 💡 **Feature Requests**: [GitHub Discussions](https://github.com/yourusername/my_financier/discussions)
- 📧 **Contact**: your.email@example.com

## 🙏 Acknowledgments

- [Kakeibo Method](https://en.wikipedia.org/wiki/Kakeibo) - Traditional Japanese budgeting
- [Groq](https://groq.com/) - AI inference platform
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Bot API wrapper

---

⭐ **Star this repo if you find it helpful!**
