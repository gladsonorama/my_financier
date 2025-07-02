import os
import logging
import boto3
from botocore.exceptions import ClientError
import tempfile
from datetime import datetime, timedelta, timezone,time
# import time

logger = logging.getLogger(__name__)

# Define IST timezone (GMT+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

class S3Storage:
    def __init__(self, 
                 endpoint_url=None, 
                 aws_access_key_id=None, 
                 aws_secret_access_key=None,
                 region_name=None,
                 bucket_name=None,
                 max_backups=96,  # Keep 96 backups (24 hours at 15min intervals)
                 max_age_days=7):  # Delete backups older than 7 days
        """Initialize S3 storage with credentials from env vars if not provided"""
        
        # Use environment variables if parameters are not provided
        self.endpoint_url = endpoint_url or os.environ.get('S3_ENDPOINT_URL')
        self.aws_access_key_id = aws_access_key_id or os.environ.get('S3_ACCESS_KEY')
        self.aws_secret_access_key = aws_secret_access_key or os.environ.get('S3_SECRET_KEY')
        self.region_name = region_name or os.environ.get('S3_REGION', 'us-east-1')
        self.bucket_name = bucket_name or os.environ.get('S3_BUCKET')
        
        # Backup retention settings
        self.max_backups = int(os.environ.get('S3_MAX_BACKUPS', max_backups))
        self.max_age_days = int(os.environ.get('S3_MAX_AGE_DAYS', max_age_days))
        self.cleanup_frequency_minutes = int(os.environ.get('S3_CLEANUP_FREQUENCY_MINUTES', 60))  # Run cleanup every hour
        
        # Create S3 client
        self.s3 = boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            region_name=self.region_name,
            verify=False
        )
        
        logger.info("🔷🔷🔷 Initialized S3 storage with endpoint: %s and bucket: %s", 
                   self.endpoint_url or 'AWS Default', self.bucket_name)
        logger.info("🔷🔷🔷 Backup retention: max %d backups, max %d days, cleanup every %d minutes", 
                   self.max_backups, self.max_age_days, self.cleanup_frequency_minutes)
    
    def _ensure_bucket_exists(self):
        """Make sure the bucket exists, create it if needed"""
        try:
            self.s3.head_bucket(Bucket=self.bucket_name)
            logger.info("🔷🔷🔷 Bucket exists: %s", self.bucket_name)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == '404':
                # Bucket doesn't exist, create it
                try:
                    if self.region_name == 'us-east-1':
                        self.s3.create_bucket(Bucket=self.bucket_name)
                    else:
                        self.s3.create_bucket(
                            Bucket=self.bucket_name,
                            CreateBucketConfiguration={'LocationConstraint': self.region_name}
                        )
                    logger.info("🔷🔷🔷 Created bucket: %s", self.bucket_name)
                except Exception as create_error:
                    logger.error("❌❌❌ Failed to create bucket: %s", str(create_error))
                    raise
            else:
                logger.error("❌❌❌ Error checking bucket: %s", str(e))
                raise
    
    def upload_file(self, file_path, object_name=None):
        """Upload a file to S3 bucket"""
        if not self.bucket_name:
            raise ValueError("S3 bucket name not specified")
        
        # If object_name not specified, use file_path basename
        if object_name is None:
            object_name = os.path.basename(file_path)
        
        try:
            self._ensure_bucket_exists()
            self.s3.upload_file(file_path, self.bucket_name, object_name)
            logger.info("🔷🔷🔷 Uploaded %s to S3 as %s", file_path, object_name)
            return True
        except Exception as e:
            logger.error("❌❌❌ Failed to upload file to S3: %s", str(e))
            return False
    
    def download_file(self, object_name, file_path=None):
        """Download a file from S3 bucket"""
        if not self.bucket_name:
            raise ValueError("S3 bucket name not specified")
        
        # If file_path not specified, use a temp file
        if file_path is None:
            fd, file_path = tempfile.mkstemp()
            os.close(fd)
        
        try:
            self.s3.download_file(self.bucket_name, object_name, file_path)
            logger.info("🔷🔷🔷 Downloaded %s from S3 to %s", object_name, file_path)
            return file_path
        except Exception as e:
            logger.error("❌❌❌ Failed to download file from S3: %s", str(e))
            if os.path.exists(file_path):
                os.remove(file_path)
            return None
    
    def list_files(self, prefix=''):
        """List files in the S3 bucket with the given prefix"""
        if not self.bucket_name:
            raise ValueError("S3 bucket name not specified")
        
        try:
            response = self.s3.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix)
            if 'Contents' in response:
                return [obj['Key'] for obj in response['Contents']]
            return []
        except Exception as e:
            logger.error("❌❌❌ Failed to list files in S3: %s", str(e))
            return []
    
    def get_latest_backup(self, prefix='expenses_backup_'):
        """Get the most recent database backup from S3"""
        backups = self.list_files(prefix=prefix)
        if not backups:
            logger.warning("⚠️⚠️⚠️ No backups found in S3 with prefix: %s", prefix)
            return None
        
        # Sort by name (which includes timestamp)
        backups.sort(reverse=True)
        latest = backups[0]
        logger.info("🔷🔷🔷 Found latest backup: %s", latest)
        return latest
    
    def _get_current_time_ist(self) -> datetime:
        """Get current time in IST (GMT+5:30)"""
        return datetime.now(IST)
    
    def _parse_backup_timestamp(self, timestamp_str: str) -> datetime:
        """Parse backup timestamp and convert to IST"""
        try:
            # Parse timestamp from filename format: YYYYMMDD_HHMMSS
            dt = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
            # Assume backup timestamps are in IST
            return dt.replace(tzinfo=IST)
        except ValueError:
            raise ValueError(f"Could not parse timestamp: {timestamp_str}")
    
    def cleanup_old_backups(self, prefix='expenses_backup_'):
        """Clean up old backup files based on retention policy using IST"""
        try:
            # Get all backup files
            backups = self.list_files(prefix=prefix)
            if not backups:
                logger.info("🔷🔷🔷 No backups found for cleanup")
                return
            
            # Parse backup files with timestamps
            backup_info = []
            current_time_ist = self._get_current_time_ist()
            
            for backup in backups:
                try:
                    # Extract timestamp from filename: expenses_backup_YYYYMMDD_HHMMSS.db
                    timestamp_str = backup.replace(prefix, '').replace('.db', '')
                    backup_time = self._parse_backup_timestamp(timestamp_str)
                    age_timedelta = current_time_ist - backup_time
                    
                    backup_info.append({
                        'name': backup,
                        'timestamp': backup_time,
                        'age_days': age_timedelta.days
                    })
                except ValueError:
                    logger.warning("⚠️⚠️⚠️ Could not parse timestamp from backup: %s", backup)
                    continue
            
            if not backup_info:
                logger.info("🔷🔷🔷 No valid backup files found for cleanup")
                return
            
            # Sort by timestamp (newest first)
            backup_info.sort(key=lambda x: x['timestamp'], reverse=True)
            
            # Determine which backups to delete
            backups_to_delete = []
            
            # Keep only max_backups most recent
            if len(backup_info) > self.max_backups:
                backups_to_delete.extend(backup_info[self.max_backups:])
            
            # Delete backups older than max_age_days
            for backup in backup_info:
                if backup['age_days'] > self.max_age_days and backup not in backups_to_delete:
                    backups_to_delete.append(backup)
            
            # Delete the identified backups
            deleted_count = 0
            for backup in backups_to_delete:
                try:
                    self.s3.delete_object(Bucket=self.bucket_name, Key=backup['name'])
                    logger.info("🗑️🗑️🗑️ Deleted old backup: %s (age: %d days)", 
                              backup['name'], backup['age_days'])
                    deleted_count += 1
                except Exception as e:
                    logger.error("❌❌❌ Failed to delete backup %s: %s", backup['name'], str(e))
            
            if deleted_count > 0:
                logger.info("✅✅✅ Cleanup completed: deleted %d old backups, keeping %d", 
                          deleted_count, len(backup_info) - deleted_count)
            else:
                logger.info("🔷🔷🔷 No backups needed cleanup")
                
        except Exception as e:
            logger.error("❌❌❌ Error during backup cleanup: %s", str(e))
    
    def should_run_cleanup(self, db) -> bool:
        """Check if it's time to run cleanup based on time interval using IST"""
        last_cleanup_str = db.get_setting('last_cleanup_time')
        if not last_cleanup_str:
            return True
        
        try:
            # Parse cleanup time and convert to IST
            last_cleanup = datetime.fromisoformat(last_cleanup_str.replace('Z', '+00:00'))
            if last_cleanup.tzinfo is None:
                last_cleanup = last_cleanup.replace(tzinfo=timezone.utc)
            last_cleanup_ist = last_cleanup.astimezone(IST)
            
            current_time_ist = self._get_current_time_ist()
            time_since_cleanup = current_time_ist - last_cleanup_ist
            return time_since_cleanup.total_seconds() >= (self.cleanup_frequency_minutes * 60)
        except:
            return True
    
    def backup_database(self, db_path, db_instance=None):
        """Backup the database file to S3 with automatic cleanup using IST"""
        ist_time = self._get_current_time_ist()
        timestamp = ist_time.strftime('%Y%m%d_%H%M%S')
        object_name = f"expenses_backup_{timestamp}.db"
        
        success = self.upload_file(db_path, object_name)
        
        if success and db_instance:
            # Update last backup time in database with IST
            db_instance.set_last_backup_time(ist_time)
            
            # Check if cleanup should run based on time interval
            if self.should_run_cleanup(db_instance):
                logger.info("🔷🔷🔷 Running backup cleanup (time-based trigger)")
                self.cleanup_old_backups()
                db_instance.set_setting('last_cleanup_time', ist_time.isoformat())
        
        return success
    
    def restore_latest_database(self, target_path):
        """Restore the latest database backup from S3"""
        latest_backup = self.get_latest_backup()
        if not latest_backup:
            return False
        
        download_path = self.download_file(latest_backup)
        if not download_path:
            return False
        
        try:
            # Copy the downloaded file to the target path
            import shutil
            shutil.copy2(download_path, target_path)
            os.remove(download_path)  # Clean up temp file
            return True
        except Exception as e:
            logger.error("❌❌❌ Failed to copy database to target path: %s", str(e))
            return False

def backup_db_to_s3(db_path=None):
    """Utility function to backup the database to S3 with cleanup"""
    from expenses_sqlite import ExpensesSQLite
    
    # Create a temporary backup file
    db = ExpensesSQLite(db_path)
    backup_path = db.backup_to_file()
    
    # Upload to S3 with cleanup (pass db instance for persistent tracking)
    s3 = S3Storage()
    success = s3.backup_database(backup_path, db)
    
    # Clean up temp file
    os.remove(backup_path)
    
    return success

def restore_db_from_s3(db_path=None):
    """Utility function to restore the database from S3"""
    db_path = db_path or 'expenses.db'
    s3 = S3Storage()
    return s3.restore_latest_database(db_path)

if __name__ == "__main__":
    # Set up basic logging
    logging.basicConfig(level=logging.INFO)
    
    # Example usage
    s3 = S3Storage(
        endpoint_url=os.environ.get('S3_ENDPOINT_URL'),
        aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
        aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
        bucket_name=os.environ.get('S3_BUCKET')
    )
    
    # List existing backups
    backups = s3.list_files('expenses_backup_')
    logger.info("Existing backups: %s", backups)
    
    ## get latest backup
    latest_backup = s3.get_latest_backup('expenses_backup_')
    
    #load latest backup into sql lite
    import sqlite3
    import pandas as pd
    if latest_backup:
        restore_latest_database = s3.restore_latest_database('expenses.db')
        if restore_latest_database:
            conn = sqlite3.connect('expenses.db')
            ## show current expenses
            cursor = conn.cursor()
            ## add today's ONLY time delta to end date
            now = datetime.now()
            current_time = time(23,59,59)  # Set to end of day
            end_date =  datetime.combine(pd.to_datetime("2025-07-02").date(), current_time)
            query = f"SELECT * FROM expenses where date <= \"{end_date}\""
            print(query)
            cursor.execute(query)
            # cursor.execute()
            expenses = cursor.fetchall()
            for expense in expenses:
                logger.info("Expense: %s", expense)
            # logger.info("Current expenses in expenses.db: %s", expenses)
            conn.close()
            # os.remove(download_path)
            logger.info("Restored latest backup to expenses.db")
        else:
            logger.error("Failed to download latest backup")

    # Backup current database
    # success = backup_db_to_s3()
    # logger.info("Backup result: %s", "Success" if success else "Failed")
