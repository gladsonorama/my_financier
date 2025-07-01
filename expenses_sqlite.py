import sqlite3
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional
import os
import tempfile
import logging

logger = logging.getLogger(__name__)

class ExpensesSQLite:
    def __init__(self, db_path: str = "expenses.db"):
        """Initialize the expenses database with an optional custom path"""
        if db_path:
            # Check if the path has a directory component
            dirname = os.path.dirname(db_path)
            if dirname:  # Only create directories if there's actually a directory path
                os.makedirs(dirname, exist_ok=True)
            self.db_path = db_path
        else:
            # Default path
            self.db_path = 'expenses.db'
        
        logger.info("🔷🔷🔷 Initializing database at: %s", self.db_path)
        self._init_database()
    
    def _init_database(self):
        """Initialize the SQLite database with required tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create expenses table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TIMESTAMP NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    kakeibo_category TEXT DEFAULT 'survival',
                    description TEXT,
                    user_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (username)
                )
            ''')
            
            conn.commit()
    
    def _normalize_category(self, category: str) -> str:
        """Normalize category name to title case"""
        if not category:
            return "Miscellaneous"
        return category.strip().title()
    
    # User Management
    def create_user(self, username: str, email: str = None) -> bool:
        """Create a new user"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (username, email) VALUES (?, ?)",
                    (username, email)
                )
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False  # User already exists
    
    def get_user(self, username: str) -> Optional[Dict]:
        """Get user by username"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT username, email, created_at FROM users WHERE username = ?",
                (username,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    'username': row[0],
                    'email': row[1],
                    'created_at': row[2]
                }
            return None
    
    def list_users(self) -> List[Dict]:
        """Get all users"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT username, email, created_at FROM users")
            return [
                {'username': row[0], 'email': row[1], 'created_at': row[2]}
                for row in cursor.fetchall()
            ]
    
    # Expense Management (mirroring CSV functionality)
    def add_expense(self, amount: float, category: str, description: str, 
                   kakeibo_category: str = None, user_id: str = None):
        """Add a new expense to the database"""
        new_expense = {
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'amount': amount,
            'category': self._normalize_category(category),
            'kakeibo_category': kakeibo_category or 'survival',
            'description': description,
            'user_id': user_id or 'unknown'
        }
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO expenses (date, amount, category, kakeibo_category, description, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                new_expense['date'],
                new_expense['amount'],
                new_expense['category'],
                new_expense['kakeibo_category'],
                new_expense['description'],
                new_expense['user_id']
            ))
            conn.commit()
        
        return new_expense
    
    def get_expenses(self, start_date: str = None, end_date: str = None, 
                    category: str = None, user_id: str = None) -> pd.DataFrame:
        """Get expenses with optional filters"""
        query = "SELECT date, amount, category, kakeibo_category, description, user_id FROM expenses WHERE 1=1"
        params = []
        
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        
        if end_date:
            # Add one day to include the end date
            end_date_plus = pd.to_datetime(end_date) #+ pd.Timedelta(days=1)
            query += " AND date <= ?"
            params.append(end_date_plus.strftime('%Y-%m-%d'))
        
        if category:
            normalized_category = self._normalize_category(category)
            query += " AND category = ?"
            params.append(normalized_category)
        
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        
        query += " ORDER BY date DESC"
        
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(query, conn, params=params)
            if not df.empty:
                df['date'] = pd.to_datetime(df['date'])
                df['category'] = df['category'].apply(self._normalize_category)
            return df
    
    def get_user_expenses(self, user_id: str, start_date: str = None, 
                         end_date: str = None, category: str = None) -> pd.DataFrame:
        """Get expenses for a specific user"""
        return self.get_expenses(start_date, end_date, category, user_id)
    
    def get_monthly_expenses(self, year: int = None, month: int = None, 
                           user_id: str = None) -> pd.DataFrame:
        """Get expenses for a specific month"""
        now = datetime.now()
        year = year or now.year
        month = month or now.month
        
        start_date = f"{year}-{month:02d}-01"
        if month == 12:
            end_date = f"{year + 1}-01-01"
        else:
            end_date = f"{year}-{month + 1:02d}-01"
        
        return self.get_expenses(start_date, end_date, user_id=user_id)
    
    def get_category_summary(self, start_date: str = None, end_date: str = None, 
                           user_id: str = None) -> Dict:
        """Get spending summary by category"""
        df = self.get_expenses(start_date, end_date, user_id=user_id)
        if df.empty:
            return {}
        
        df['category'] = df['category'].apply(self._normalize_category)
        summary = df.groupby('category')['amount'].agg(['sum', 'count']).to_dict('index')
        return {cat: {'total': data['sum'], 'count': data['count']} for cat, data in summary.items()}
    
    def get_kakeibo_summary(self, start_date: str = None, end_date: str = None, 
                          user_id: str = None) -> Dict:
        """Get spending summary by kakeibo category"""
        df = self.get_expenses(start_date, end_date, user_id=user_id)
        if df.empty:
            return {}
        
        summary = df.groupby('kakeibo_category')['amount'].agg(['sum', 'count']).to_dict('index')
        return {cat: {'total': data['sum'], 'count': data['count']} for cat, data in summary.items()}
    
    def get_kakeibo_balance_analysis(self, start_date: str = None, end_date: str = None, 
                                   user_id: str = None) -> Dict:
        """Analyze kakeibo balance and provide recommendations"""
        kakeibo_summary = self.get_kakeibo_summary(start_date, end_date, user_id)
        if not kakeibo_summary:
            return {}
        
        total_spending = sum(data['total'] for data in kakeibo_summary.values())
        
        # Kakeibo recommended percentages
        recommended = {
            'survival': 0.50,  # 50% for needs
            'optional': 0.30,  # 30% for wants
            'culture': 0.10,   # 10% for culture/self-improvement
            'extra': 0.10      # 10% for unexpected
        }
        
        analysis = {}
        for category in ['survival', 'optional', 'culture', 'extra']:
            actual_amount = kakeibo_summary.get(category, {}).get('total', 0)
            actual_percentage = (actual_amount / total_spending * 100) if total_spending > 0 else 0
            recommended_percentage = recommended[category] * 100
            
            analysis[category] = {
                'actual_amount': actual_amount,
                'actual_percentage': actual_percentage,
                'recommended_percentage': recommended_percentage,
                'variance': actual_percentage - recommended_percentage,
                'status': 'over' if actual_percentage > recommended_percentage else 'under'
            }
        
        return analysis
    
    def get_top_expenses(self, limit: int = 10, start_date: str = None, 
                        end_date: str = None, user_id: str = None) -> pd.DataFrame:
        """Get top expenses by amount"""
        df = self.get_expenses(start_date, end_date, user_id=user_id)
        if df.empty:
            return df
        
        df['category'] = df['category'].apply(self._normalize_category)
        return df.nlargest(limit, 'amount')
    
    def get_spending_trends(self, months: int = 6, user_id: str = None) -> Dict:
        """Get monthly spending trends"""
        end_date = datetime.now()
        trends = {}
        
        for i in range(months):
            month_date = datetime(end_date.year, end_date.month - i, 1) if end_date.month > i else datetime(end_date.year - 1, 12 - (i - end_date.month), 1)
            df = self.get_monthly_expenses(month_date.year, month_date.month, user_id)
            
            month_key = month_date.strftime('%Y-%m')
            trends[month_key] = {
                'total': df['amount'].sum() if not df.empty else 0,
                'transactions': len(df)
            }
        
        return trends
    
    def get_user_stats(self, user_id: str) -> Dict:
        """Get comprehensive statistics for a user"""
        df = self.get_user_expenses(user_id)
        if df.empty:
            return {}
        
        return {
            'total_expenses': df['amount'].sum(),
            'total_transactions': len(df),
            'average_expense': df['amount'].mean(),
            'first_expense_date': df['date'].min().strftime('%Y-%m-%d'),
            'last_expense_date': df['date'].max().strftime('%Y-%m-%d'),
            'top_category': df.groupby('category')['amount'].sum().idxmax(),
            'monthly_average': df['amount'].sum() / max(1, df['date'].dt.to_period('M').nunique())
        }
    
    # Additional methods for S3 backup/restore
    def backup_to_file(self, backup_path=None) -> str:
        """Backup database to a file and return the file path"""
        if not backup_path:
            # Create a temp file with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = f"expenses_backup_{timestamp}.db"
        
        try:
            # Create a copy of the database file
            import shutil
            shutil.copy2(self.db_path, backup_path)
            logger.info("🔷🔷🔷 Database backed up to: %s", backup_path)
            return backup_path
        except Exception as e:
            logger.error("❌❌❌ Database backup failed: %s", str(e))
            raise
    
    def restore_from_file(self, backup_path: str) -> bool:
        """Restore database from a backup file"""
        try:
            # Check if backup file exists
            if not os.path.exists(backup_path):
                logger.error("❌❌❌ Backup file not found: %s", backup_path)
                return False
            
            # Close any open connections to current db
            try:
                conn = sqlite3.connect(self.db_path)
                conn.close()
            except:
                pass
            
            # Replace current db with backup
            import shutil
            shutil.copy2(backup_path, self.db_path)
            
            # Re-initialize to ensure tables exist
            self._init_database()
            logger.info("🔷🔷🔷 Database restored from: %s", backup_path)
            return True
        except Exception as e:
            logger.error("❌❌❌ Database restore failed: %s", str(e))
            return False
    
    def normalize_existing_data(self):
        """Normalize all existing category data in the database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get all expenses
            cursor.execute("SELECT id, category FROM expenses")
            expenses = cursor.fetchall()
            
            # Update each expense with normalized category
            for expense_id, category in expenses:
                normalized_category = self._normalize_category(category)
                cursor.execute(
                    "UPDATE expenses SET category = ? WHERE id = ?",
                    (normalized_category, expense_id)
                )
            
            conn.commit()
            logger.info("🔷🔷🔷 Normalized existing category data")

if __name__ == "__main__":
    db = ExpensesSQLite()
    
    # Create a test user
    db.create_user('user123', 'user123@example.com')
    
    # Add test expenses
    db.add_expense(1500, 'groceries', 'Bought vegetables and fruits', 'survival', 'user123')
    db.add_expense(2000, 'entertainment', 'Movie tickets', 'optional', 'user123')
    
    logger.info("Category Summary: %s", db.get_category_summary(user_id='user123'))
    logger.info("User Stats: %s", db.get_user_stats('user123'))
