import os
import logging
import boto3
from botocore.exceptions import ClientError
import tempfile
from datetime import datetime
import time

logger = logging.getLogger(__name__)

class S3Storage:
    def __init__(self, 
                 endpoint_url=None, 
                 aws_access_key_id=None, 
                 aws_secret_access_key=None,
                 region_name=None,
                 bucket_name=None):
        """Initialize S3 storage with credentials from env vars if not provided"""
        
        # Use environment variables if parameters are not provided
        self.endpoint_url = endpoint_url or os.environ.get('S3_ENDPOINT_URL')
        self.aws_access_key_id = aws_access_key_id or os.environ.get('S3_ACCESS_KEY')
        self.aws_secret_access_key = aws_secret_access_key or os.environ.get('S3_SECRET_KEY')
        self.region_name = region_name or os.environ.get('S3_REGION', 'us-east-1')
        self.bucket_name = bucket_name or os.environ.get('S3_BUCKET')
        
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
    
    def backup_database(self, db_path):
        """Backup the database file to S3"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        object_name = f"expenses_backup_{timestamp}.db"
        
        return self.upload_file(db_path, object_name)
    
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
    """Utility function to backup the database to S3"""
    from expenses_sqlite import ExpensesSQLite
    
    # Create a temporary backup file
    db = ExpensesSQLite(db_path)
    backup_path = db.backup_to_file()
    
    # Upload to S3
    s3 = S3Storage()
    success = s3.upload_file(backup_path, f"expenses_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
    
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
    
    # Backup current database
    success = backup_db_to_s3()
    logger.info("Backup result: %s", "Success" if success else "Failed")
