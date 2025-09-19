from parameterized import parameterized
import unittest
from unittest.mock import patch, MagicMock, mock_open
import multiprocessing as mp
from pathlib import Path


from url_ingester import FolderWatcherInfo, folder_watcher, EmailInfo, email_watcher
from notification_wrapper import NotificationWrapper
from fanfic_info import FanficInfo
from config_models import (
    AppConfig,
    FolderWatcherConfig,
    SMTPConfig,
    EmailConfig,
    CalibreConfig,
    AppriseConfig,
    PushbulletConfig,
)


class TestUrlIngester(unittest.TestCase):
    @parameterized.expand(
        [
            (
                "basic_config",
                "/tmp/url_folder",
                60,
                True,
            ),
            (
                "different_config",
                "/var/url_watch",
                30,
                False,
            ),
            (
                "minimal_config",
                "/home/urls",
                5,
                True,
            ),
        ]
    )
    @patch("config_models.ConfigManager.load_config")
    @patch("pathlib.Path.mkdir")
    def test_folder_watcher_info_init_basic(
        self, name, folder_path, sleep_time, ffnet_disable, mock_mkdir, mock_load_config
    ):
        # Setup mock config
        mock_config = AppConfig(
            folder_watcher=FolderWatcherConfig(
                folder_path=folder_path,
                sleep_time=sleep_time,
                ffnet_disable=ffnet_disable,
            ),
            calibre=CalibreConfig(path="/tmp/calibre"),
            smtp=SMTPConfig(),
            apprise=AppriseConfig(),
            pushbullet=PushbulletConfig(),
        )
        mock_load_config.return_value = mock_config

        folder_info = FolderWatcherInfo("test_path.toml")

        self.assertEqual(folder_info.folder_path, folder_path)
        self.assertEqual(folder_info.sleep_time, sleep_time)
        self.assertEqual(folder_info.ffnet_disable, ffnet_disable)
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @parameterized.expand(
        [
            (
                "basic_config",
                "testuser",
                "test_password",
                "test_server",
                "test_mailbox",
                10,
            ),
            (
                "different_config",
                "anotheruser",
                "another_password",
                "another_server",
                "another_mailbox",
                20,
            ),
            (
                "minimal_config",
                "minimaluser",
                "min_pass",
                "min_server",
                "INBOX",
                5,
            ),
        ]
    )
    @patch("config_models.ConfigManager.load_config")
    def test_email_info_init_basic(
        self, name, email, password, server, mailbox, sleep_time, mock_load_config
    ):
        # Setup mock config
        mock_config = AppConfig(
            folder_watcher=FolderWatcherConfig(folder_path="/tmp/folder"),
            email=EmailConfig(
                email=email,
                password=password,
                server=server,
                mailbox=mailbox,
                sleep_time=sleep_time,
            ),
            calibre=CalibreConfig(path="/tmp/calibre"),
            smtp=SMTPConfig(),
            apprise=AppriseConfig(),
            pushbullet=PushbulletConfig(),
        )
        mock_load_config.return_value = mock_config

        email_info = EmailInfo("test_path.toml")

        self.assertEqual(email_info.email, email)
        self.assertEqual(email_info.password, password)
        self.assertEqual(email_info.server, server)
        self.assertEqual(email_info.mailbox, mailbox)
        self.assertEqual(email_info.sleep_time, sleep_time)

    @parameterized.expand(
        [
            ("ffnet_enabled", True),
            ("ffnet_disabled", False),
        ]
    )
    @patch("config_models.ConfigManager.load_config")
    def test_email_info_init_ffnet_disable(self, name, ffnet_disable, mock_load_config):
        # Setup mock config with ffnet_disable setting
        mock_config = AppConfig(
            folder_watcher=FolderWatcherConfig(folder_path="/tmp/folder"),
            email=EmailConfig(
                email="testuser",
                password="test_password",
                server="test_server",
                ffnet_disable=ffnet_disable,
            ),
            calibre=CalibreConfig(path="/tmp/calibre"),
            smtp=SMTPConfig(),
            apprise=AppriseConfig(),
            pushbullet=PushbulletConfig(),
        )
        mock_load_config.return_value = mock_config

        email_info = EmailInfo("test_path.toml")

        self.assertEqual(email_info.ffnet_disable, ffnet_disable)

    @parameterized.expand(
        [
            ("scenario_1", ["https://archiveofourown.org/works/123", "https://fanfiction.net/s/456"]),
            ("scenario_2", ["https://example.com/story/789"]),
            ("empty_folder", []),
        ]
    )
    @patch("config_models.ConfigManager.load_config")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.glob")
    @patch("builtins.open", new_callable=mock_open)
    @patch("pathlib.Path.unlink")
    def test_folder_watcher_get_urls(
        self, name, expected_urls, mock_unlink, mock_file_open, mock_glob, mock_mkdir, mock_load_config
    ):
        # Setup mock config
        mock_config = AppConfig(
            folder_watcher=FolderWatcherConfig(
                folder_path="/tmp/folder",
                sleep_time=60,
                ffnet_disable=False,
            ),
            calibre=CalibreConfig(path="/tmp/calibre"),
            smtp=SMTPConfig(),
            apprise=AppriseConfig(),
            pushbullet=PushbulletConfig(),
        )
        mock_load_config.return_value = mock_config

        # Mock files and their content
        mock_file_paths = []
        file_contents = {}
        for i, url in enumerate(expected_urls):
            file_path = f"/tmp/folder/test{i}.url"
            mock_file_paths.append(Path(file_path))
            file_contents[file_path] = url

        # Mock the glob method to return our mock file paths
        mock_glob.return_value = mock_file_paths
        
        # Set up mock_file_open to return the right content for each file
        def side_effect(file_path, *args, **kwargs):
            file_path_str = str(file_path)
            if file_path_str in file_contents:
                return mock_open(read_data=file_contents[file_path_str]).return_value
            return mock_open(read_data="").return_value

        mock_file_open.side_effect = side_effect

        folder_info = FolderWatcherInfo("test_path.toml")
        urls = folder_info.get_urls()

        self.assertEqual(len(urls), len(expected_urls))
        for url in expected_urls:
            self.assertIn(url, urls)

        # Verify files were unlinked
        self.assertEqual(mock_unlink.call_count, len(expected_urls))

    @parameterized.expand(
        [
            ("scenario_1", ["url1", "url2"]),
            ("scenario_2", ["url3", "url4", "url5"]),
            ("empty_urls", []),
        ]
    )
    @patch("url_ingester.geturls.get_urls_from_imap")
    @patch("config_models.ConfigManager.load_config")
    def test_email_info_get_urls(
        self, name, expected_urls, mock_load_config, mock_get_urls_from_imap
    ):
        # Setup mock config (email tests need folder_watcher due to new config structure)
        mock_config = AppConfig(
            folder_watcher=FolderWatcherConfig(
                folder_path="/tmp/test_folder",
                sleep_time=60,
                ffnet_disable=False,
            ),
            email=EmailConfig(
                email="testuser",
                password="test_password",
                server="test_server",
                mailbox="test_mailbox",
                sleep_time=10,
            ),
            calibre=CalibreConfig(path="/tmp/calibre"),
            smtp=SMTPConfig(),
            apprise=AppriseConfig(),
            pushbullet=PushbulletConfig(),
        )
        mock_load_config.return_value = mock_config

        # Setup mock URL return
        mock_get_urls_from_imap.return_value = expected_urls

        email_info = EmailInfo("test_path.toml")
        result = email_info.get_urls()

        self.assertEqual(result, expected_urls)
        mock_get_urls_from_imap.assert_called_once()


if __name__ == "__main__":
    unittest.main()
