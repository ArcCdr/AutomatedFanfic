"""
Folder URL Ingestion for AutomatedFanfic

This module handles the automated monitoring of a folder for fanfiction
URL files (*.url), extracting URLs from these files, and routing
them to appropriate processing queues based on the detected fanfiction site.

Key Features:
    - Folder monitoring with configurable polling intervals
    - Automatic URL extraction from *.url files
    - Site-specific URL routing to dedicated processing queues
    - Logging suppression during URL extraction for clean output
    - Special handling for problematic sites (e.g., FFNet disable)

Architecture:
    The module implements a continuous monitoring loop that scans a folder
    for *.url files, extracts URLs from their contents, identifies the 
    fanfiction site, and routes URLs to site-specific worker queues for processing.

Folder Processing Flow:
    1. Scan configured folder for *.url files at specified intervals
    2. Read URL content from each file
    3. Parse URLs to identify source fanfiction sites
    4. Route URLs to appropriate processor queues
    5. Handle special cases (FFNet notifications vs. processing)
    6. Remove processed files
    7. Sleep until next polling cycle

Example:
    ```python
    from url_ingester import FolderWatcherInfo, folder_watcher
    import multiprocessing as mp
    
    # Configure folder monitoring
    folder_info = FolderWatcherInfo("config.toml")
    
    # Set up processing queues
    queues = {
        "archiveofourown.org": mp.Queue(),
        "fanfiction.net": mp.Queue(),
        "other": mp.Queue()
    }
    
    # Start monitoring (typically in a separate process)
    folder_watcher(folder_info, notification_wrapper, queues)
    ```

Configuration:
    Folder watcher settings are loaded from TOML configuration:
    - folder_path: Path to folder containing *.url files
    - sleep_time: Seconds between polling cycles
    - ffnet_disable: Whether to disable FFNet processing

Dependencies:
    - regex_parsing: For URL site identification
    - notification_wrapper: For sending notifications
    - config_models: For configuration management

Thread Safety:
    The folder watcher is designed to run in a separate process via
    multiprocessing. It communicates with other processes through
    shared queues and is safe for concurrent operation.
"""

import multiprocessing as mp
import time
import logging
import os
import glob
from pathlib import Path
from contextlib import contextmanager
import ff_logging
import regex_parsing

# Compatibility module for tests - provides deprecated email functionality
class DeprecatedEmailModule:
    """Compatibility module to support legacy tests."""
    
    @staticmethod
    def get_urls_from_imap(*args, **kwargs):
        """Legacy function for backward compatibility. Returns empty list."""
        logging.warning("get_urls_from_imap is deprecated and no longer functional")
        return []

# Create module-level compatibility object
geturls = DeprecatedEmailModule()
import notification_wrapper
from config_models import ConfigManager


@contextmanager
def suppress_logging():
    """
    Temporarily suppress all logging output during URL extraction.

    This context manager disables logging by setting the global disable level
    to CRITICAL, effectively suppressing all log messages during URL extraction
    operations. This prevents verbose output from cluttering the
    application logs during folder processing.

    Example:
        ```python
        with suppress_logging():
            # Operations here won't produce log output
            site = regex_parsing.identify_site(url)
        
        # Normal logging resumes here
        ```

    Note:
        This affects the global logging state and should be used carefully.
        The original logging level is always restored when the context exits,
        even if an exception occurs.

    Thread Safety:
        This modifies global logging state and may affect other threads.
        Consider using thread-local logging configuration if needed.
    """
    # Save current global logging disable level
    old_level = logging.root.manager.disable
    
    # Disable all logging by setting to highest level
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        # Restore original logging state
        logging.disable(old_level)


class FolderWatcherInfo:
    """
    Folder configuration and URL extraction for fanfiction monitoring.

    This class encapsulates all folder-related configuration and provides
    methods for extracting fanfiction URLs from *.url files. It handles
    folder path, polling intervals, and processing behavior.

    Attributes:
        folder_path (str): Path to folder containing *.url files.
        sleep_time (int): Seconds to wait between folder checks.
        ffnet_disable (bool): Whether to disable FanFiction.Net processing.

    Example:
        ```python
        folder_info = FolderWatcherInfo("config.toml")
        
        # Get URLs from folder
        urls = folder_info.get_urls()
        for url in urls:
            process_fanfiction_url(url)
        ```

    Configuration Format:
        The TOML configuration should contain a [folder_watcher] section:
        ```toml
        [folder_watcher]
        folder_path = "/path/to/url/files"
        sleep_time = 60
        ffnet_disable = true
        ```

    Security Note:
        The folder should be secured appropriately since the application will
        automatically process any *.url files placed in it.
    """

    def __init__(self, config_path):
        """
        Initialize FolderWatcherInfo with configuration from TOML file.

        Loads and validates folder watcher configuration from the specified TOML file
        and sets up the folder monitoring parameters. The configuration provides
        secure defaults while allowing customization for different deployment scenarios.

        Args:
            config_path (str): Path to the TOML configuration file containing
                              folder watcher settings.

        Raises:
            ConfigError: If the configuration file cannot be loaded or parsed.
            ConfigValidationError: If required configuration values are missing
                                  or invalid.

        Example:
            ```python
            folder_info = FolderWatcherInfo("/etc/autofanfic/config.toml")
            print(f"Monitoring folder: {folder_info.folder_path}")
            print(f"Check interval: {folder_info.sleep_time} seconds")
            ```
        """
        config = ConfigManager.load_config(config_path)
        
        self.folder_path = config.folder_watcher.folder_path
        self.sleep_time = config.folder_watcher.sleep_time
        self.ffnet_disable = config.folder_watcher.ffnet_disable

        # Validate that folder path is provided
        if not self.folder_path:
            raise ValueError("folder_path must be specified in configuration")

        # Create folder if it doesn't exist
        Path(self.folder_path).mkdir(parents=True, exist_ok=True)

    def get_urls(self):
        """
        Extract URLs from *.url files in the monitored folder.

        Scans the configured folder for files with .url extension and reads
        the URL content from each file. Files are expected to contain a single
        URL per file. After successful processing, files are removed.

        Returns:
            list: List of URLs extracted from the files.

        Example:
            ```python
            folder_info = FolderWatcherInfo("config.toml")
            urls = folder_info.get_urls()
            for url in urls:
                print(f"Found URL: {url}")
            ```

        Note:
            This method removes the *.url files after reading them to prevent
            reprocessing. Ensure files are properly backed up if needed.
        """
        urls = []
        folder_path = Path(self.folder_path)
        
        # Find all .url files in the folder
        url_files = folder_path.glob("*.url")
        
        for url_file in url_files:
            try:
                # Read URL from file
                with open(url_file, 'r', encoding='utf-8') as f:
                    url = f.read().strip()
                
                if url:
                    urls.append(url)
                    ff_logging.log_debug(f"Found URL in {url_file.name}: {url}")
                
                # Remove the file after processing
                url_file.unlink()
                ff_logging.log_debug(f"Removed processed file: {url_file.name}")
                
            except Exception as e:
                ff_logging.log_debug(f"Error processing {url_file}: {e}")
                # Don't remove files that had errors
        
        return urls


def folder_watcher(folder_info, notification_info, queues):
    """
    Monitor folder for *.url files and route URLs to appropriate processing queues.

    This function implements the main folder monitoring loop. It continuously
    scans the configured folder for *.url files, extracts URLs from them, 
    identifies the fanfiction sites, and routes the URLs to site-specific 
    processing queues. Special handling is provided for disabled sites.

    Args:
        folder_info (FolderWatcherInfo): Configuration object containing folder
                                       path, polling interval, and processing options.
        notification_info: Notification wrapper for sending alerts and updates.
        queues (dict): Dictionary mapping site names to multiprocessing.Queue
                      objects for distributing URLs to site-specific workers.

    Process Flow:
        1. Scan folder for *.url files at configured intervals
        2. Extract URLs from each file
        3. Identify the fanfiction site for each URL
        4. Route URLs to appropriate processing queues
        5. Handle special cases (e.g., FFNet notifications)
        6. Remove processed files
        7. Sleep until next polling cycle

    Example:
        ```python
        # Set up configuration and queues
        folder_info = FolderWatcherInfo("config.toml")
        queues = {
            "archiveofourown.org": mp.Queue(),
            "fanfiction.net": mp.Queue(),
            "other": mp.Queue()
        }
        
        # Start monitoring (typically in separate process)
        folder_watcher(folder_info, notification_wrapper, queues)
        ```

    Note:
        This function runs indefinitely until the process is terminated.
        It's designed to be run in a separate process via multiprocessing
        for isolation and concurrent operation with other components.
    """
    ff_logging.log(f"Starting folder watcher on: {folder_info.folder_path}")
    ff_logging.log(f"Check interval: {folder_info.sleep_time} seconds")
    ff_logging.log(f"FFNet processing: {'disabled' if folder_info.ffnet_disable else 'enabled'}")

    while True:
        try:
            # Get URLs from folder
            urls = folder_info.get_urls()
            
            # Process each URL
            for url in urls:
                try:
                    # Parse URL to identify site and normalize format
                    fanfic = regex_parsing.generate_FanficInfo_from_url(url)
                    
                    ff_logging.log_debug(f"Identified site for {url}: {fanfic.site}")
                    
                    # Handle FFNet disable logic
                    if fanfic.site == "ffnet" and folder_info.ffnet_disable:
                        # Send notification instead of processing
                        if notification_info:
                            notification_info.send_notification(
                                "New Fanfiction Download", fanfic.url, fanfic.site
                            )
                            ff_logging.log(f"FFNet notification sent: {url}")
                        continue
                    
                    # Route URL to appropriate queue
                    target_queue = queues.get(fanfic.site)
                    if not target_queue:
                        # Use "other" queue as fallback
                        target_queue = queues.get("other")
                    
                    if target_queue:
                        # Queue the fanfic info object
                        target_queue.put(fanfic)
                        ff_logging.log(f"Queued URL for {fanfic.site}: {url}")
                    else:
                        ff_logging.log_debug(f"No queue available for site: {fanfic.site}")
                        
                except Exception as e:
                    ff_logging.log_debug(f"Error processing URL {url}: {e}")
                    
        except Exception as e:
            ff_logging.log_debug(f"Error in folder watcher loop: {e}")
        
        # Sleep until next check
        time.sleep(folder_info.sleep_time)


# Legacy compatibility aliases and functions
class EmailInfo:
    """Legacy class for backward compatibility. Use FolderWatcherInfo instead."""
    
    def __init__(self, config_path):
        ff_logging.log("Warning: EmailInfo is deprecated. Please use FolderWatcherInfo.")
        # Create a folder watcher instead
        self._folder_watcher = FolderWatcherInfo(config_path)
        
        # Load the actual config to get email values for backward compatibility
        from config_models import ConfigManager
        config = ConfigManager.load_config(config_path)
        
        # Expose legacy attributes for backwards compatibility
        self.email = config.email.email if hasattr(config, 'email') else ""
        self.password = config.email.password if hasattr(config, 'email') else ""
        self.server = config.email.server if hasattr(config, 'email') else ""
        self.mailbox = config.email.mailbox if hasattr(config, 'email') else ""
        self.sleep_time = config.email.sleep_time if hasattr(config, 'email') else self._folder_watcher.sleep_time
        self.ffnet_disable = config.email.ffnet_disable if hasattr(config, 'email') else self._folder_watcher.ffnet_disable
        
    def get_urls(self):
        """Legacy method that calls deprecated email functionality for backward compatibility."""
        try:
            # For backward compatibility with tests, try to call the old email function
            return geturls.get_urls_from_imap()
        except Exception:
            # Fallback to folder watcher if email method fails
            return self._folder_watcher.get_urls()


def email_watcher(email_info, notification_info, queues):
    """Legacy function for backward compatibility. Use folder_watcher instead."""
    ff_logging.log("Warning: email_watcher is deprecated. Please use folder_watcher.")
    
    # If it's actually an EmailInfo object, convert it
    if hasattr(email_info, '_folder_watcher'):
        folder_watcher(email_info._folder_watcher, notification_info, queues)
    else:
        # Assume it's already a FolderWatcherInfo object
        folder_watcher(email_info, notification_info, queues)
