import logging
import sys
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
# This ensures log files can be written
if not os.path.exists('logs'):
    os.makedirs('logs')

# Create logger instance with application name
logger = logging.getLogger("palapal-ai")
logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture all log levels

# Create formatter for log messages
# Includes timestamp, logger name, level, file location, and message
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Console handler - logs to stdout (terminal)
# Set to INFO level to avoid cluttering console with DEBUG messages
#console_handler = logging.StreamHandler(sys.stdout)
#console_handler.setLevel(logging.INFO)  # Only INFO and above to console
#console_handler.setFormatter(formatter)

# File handler with rotation - logs to file with automatic rotation
# Prevents log files from growing too large
file_handler = RotatingFileHandler(
    'logs/app.log',  # Log file path
    maxBytes=10*1024*1024,  # 10MB max file size before rotation
    backupCount=5,  # Keep 5 backup files (app.log.1, app.log.2, etc.)
    encoding='utf-8'  # UTF-8 encoding for international characters
)
file_handler.setLevel(logging.DEBUG)  # DEBUG level to file (captures everything)
file_handler.setFormatter(formatter)

# Add file handler only once (avoids duplicate lines if module is imported again)
if not logger.handlers:
    logger.addHandler(file_handler)

# Prevent duplicate logs
# Without this, logs might propagate to parent loggers and appear multiple times
logger.propagate = False

# Log initialization message
# logger.info("Logger initialized - logging to console and logs/app.log")
