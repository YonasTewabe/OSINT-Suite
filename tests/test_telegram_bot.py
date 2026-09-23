import unittest
from unittest.mock import MagicMock
from telegram_bot import (
    _extract_target_arg,
    create_bot_application,
    AWAIT_HOLEHE_EMAIL,
    AWAIT_US_EMAIL,
    AWAIT_US_USERNAME,
)

class TestTelegramBotHelpers(unittest.IsolatedAsyncioTestCase):
    def test_extract_target_arg_from_args(self):
        update = MagicMock()
        context = MagicMock()
        context.args = ["test@example.com"]
        arg = _extract_target_arg(update, context)
        self.assertEqual(arg, "test@example.com")

    def test_extract_target_arg_from_message_text(self):
        update = MagicMock()
        context = MagicMock()
        context.args = []
        update.message.text = "/holhe user@domain.com"
        arg = _extract_target_arg(update, context)
        self.assertEqual(arg, "user@domain.com")

        update.message.text = "/us-email target@domain.com"
        arg = _extract_target_arg(update, context)
        self.assertEqual(arg, "target@domain.com")

        update.message.text = "/us-name johndoe"
        arg = _extract_target_arg(update, context)
        self.assertEqual(arg, "johndoe")

    def test_extract_target_arg_empty(self):
        update = MagicMock()
        context = MagicMock()
        context.args = []
        update.message.text = "/holhe"
        arg = _extract_target_arg(update, context)
        self.assertIsNone(arg)

        update.message.text = "/us-email"
        arg = _extract_target_arg(update, context)
        self.assertIsNone(arg)

    def test_create_bot_application(self):
        # Verify app builds without exceptions with a dummy token format
        token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
        app = create_bot_application(token)
        self.assertIsNotNone(app)
        self.assertTrue(len(app.handlers[0]) > 0)

    async def test_cmd_app(self):
        from telegram_bot import cmd_app
        import os
        from unittest.mock import AsyncMock
        update = MagicMock()
        context = MagicMock()
        update.message.reply_text = AsyncMock()

        # Without URL
        orig_url = os.environ.get("TELEGRAM_WEBAPP_URL")
        os.environ["TELEGRAM_WEBAPP_URL"] = ""
        await cmd_app(update, context)
        self.assertTrue(update.message.reply_text.called)
        self.assertIn("not configured", update.message.reply_text.call_args[0][0])

        # With URL
        os.environ["TELEGRAM_WEBAPP_URL"] = "https://example.com"
        await cmd_app(update, context)
        self.assertTrue(update.message.reply_text.called)
        self.assertIn("OSINT Suite Mini App", update.message.reply_text.call_args[0][0])
        if orig_url is not None:
            os.environ["TELEGRAM_WEBAPP_URL"] = orig_url


class TestAutoDeleteEngine(unittest.IsolatedAsyncioTestCase):
    def test_get_auto_delete_delay_seconds(self):
        import os
        from telegram_bot import _get_auto_delete_delay_seconds

        orig = os.environ.get("TELEGRAM_AUTO_DELETE_MINUTES")
        try:
            # Not set
            os.environ.pop("TELEGRAM_AUTO_DELETE_MINUTES", None)
            self.assertIsNone(_get_auto_delete_delay_seconds())

            # 2 minutes -> 120 seconds
            os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = "2"
            self.assertEqual(_get_auto_delete_delay_seconds(), 120.0)

            # 0.5 minutes -> 30 seconds
            os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = "0.5"
            self.assertEqual(_get_auto_delete_delay_seconds(), 30.0)

            # 0 or negative -> disabled (None)
            os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = "0"
            self.assertIsNone(_get_auto_delete_delay_seconds())
            os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = "-1"
            self.assertIsNone(_get_auto_delete_delay_seconds())

            # Invalid string -> None
            os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = "invalid"
            self.assertIsNone(_get_auto_delete_delay_seconds())
        finally:
            if orig is not None:
                os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = orig
            else:
                os.environ.pop("TELEGRAM_AUTO_DELETE_MINUTES", None)

    def test_append_auto_delete_notice(self):
        from telegram_bot import _append_auto_delete_notice

        # Disabled (None or <= 0)
        self.assertEqual(_append_auto_delete_notice("Results", None), "Results")
        self.assertEqual(_append_auto_delete_notice("Results", 0), "Results")

        # 2 minutes
        res_2m = _append_auto_delete_notice("Results", 120.0)
        self.assertIn("Results", res_2m)
        self.assertIn("auto-delete in 2 minutes", res_2m)

        # 1 minute (singular)
        res_1m = _append_auto_delete_notice("Results", 60.0)
        self.assertIn("auto-delete in 1 minute.", res_1m)

    def test_track_and_clear_chain_messages(self):
        from telegram_bot import (
            _track_chain_message,
            _get_and_clear_chain_messages,
            _clear_chain_messages,
        )
        context = MagicMock()
        context.user_data = {}

        # Add message IDs
        _track_chain_message(context, 101)
        _track_chain_message(context, 102)
        _track_chain_message(context, 101)  # duplicate should be ignored
        _track_chain_message(context, None)  # None ignored

        self.assertEqual(context.user_data["chain_message_ids"], [101, 102])

        # Get and clear
        popped = _get_and_clear_chain_messages(context)
        self.assertEqual(popped, [101, 102])
        self.assertNotIn("chain_message_ids", context.user_data)

        # Test clear directly
        _track_chain_message(context, 201)
        _clear_chain_messages(context)
        self.assertNotIn("chain_message_ids", context.user_data)

    async def test_delete_messages_delayed_bulk(self):
        from telegram_bot import _delete_messages_delayed
        from unittest.mock import AsyncMock

        bot = MagicMock()
        bot.delete_messages = AsyncMock()

        # Run with delay=0.01 for fast testing
        await _delete_messages_delayed(bot, chat_id=12345, message_ids=[1, 2, 3], delay=0.01)

        bot.delete_messages.assert_awaited_once_with(chat_id=12345, message_ids=[1, 2, 3])

    async def test_delete_messages_delayed_fallback_individual(self):
        from telegram_bot import _delete_messages_delayed
        from unittest.mock import AsyncMock

        bot = MagicMock()
        # Simulate bulk delete failure
        bot.delete_messages = AsyncMock(side_effect=Exception("Bulk deletion not supported"))
        bot.delete_message = AsyncMock()

        await _delete_messages_delayed(bot, chat_id=12345, message_ids=[10, 20], delay=0.01)

        self.assertEqual(bot.delete_message.await_count, 2)
        bot.delete_message.assert_any_await(chat_id=12345, message_id=10)
        bot.delete_message.assert_any_await(chat_id=12345, message_id=20)

    async def test_schedule_chain_deletion(self):
        import os
        from telegram_bot import _schedule_chain_deletion, _track_chain_message
        from unittest.mock import AsyncMock

        orig = os.environ.get("TELEGRAM_AUTO_DELETE_MINUTES")
        try:
            os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = "2"
            context = MagicMock()
            context.user_data = {}
            context.bot = MagicMock()
            context.bot.delete_messages = AsyncMock()
            context.application = None

            _track_chain_message(context, 100)
            _track_chain_message(context, 101)

            # Schedule deletion with short delay
            task = _schedule_chain_deletion(context, chat_id=999, delay=0.02)
            self.assertIsNotNone(task)
            self.assertEqual(context.user_data.get("chain_message_ids"), None)

            # Await task to verify it completes
            await task
            context.bot.delete_messages.assert_awaited_once_with(chat_id=999, message_ids=[100, 101])
        finally:
            if orig is not None:
                os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = orig
            else:
                os.environ.pop("TELEGRAM_AUTO_DELETE_MINUTES", None)


class TestConversationFlowTracking(unittest.IsolatedAsyncioTestCase):
    async def test_cmd_cancel_clears_chain(self):
        from telegram_bot import cmd_cancel, _track_chain_message
        from unittest.mock import AsyncMock

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.user_data = {}

        _track_chain_message(context, 555)
        res = await cmd_cancel(update, context)
        self.assertEqual(res, -1)  # ConversationHandler.END
        self.assertNotIn("chain_message_ids", context.user_data)

    async def test_holehe_flow_tracking_and_auto_delete(self):
        import os
        from unittest.mock import AsyncMock, patch
        from telegram_bot import cmd_holhe, step_holehe_email

        orig = os.environ.get("TELEGRAM_AUTO_DELETE_MINUTES")
        try:
            os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = "2"
            context = MagicMock()
            context.args = []
            context.user_data = {}
            context.bot = MagicMock()
            context.bot.delete_messages = AsyncMock()
            context.application = None

            # 1. User sends /holhe (message id 10)
            update_cmd = MagicMock()
            update_cmd.message.message_id = 10
            update_cmd.message.text = "/holhe"
            prompt_mock = MagicMock()
            prompt_mock.message_id = 11
            update_cmd.message.reply_text = AsyncMock(return_value=prompt_mock)

            await cmd_holhe(update_cmd, context)

            self.assertEqual(context.user_data["chain_message_ids"], [10, 11])

            # 2. User sends email (message id 12)
            update_email = MagicMock()
            update_email.effective_chat.id = 777
            update_email.message.message_id = 12
            update_email.message.text = "test@example.com"
            status_mock = MagicMock()
            status_mock.message_id = 13
            status_mock.edit_text = AsyncMock()
            update_email.message.reply_text = AsyncMock(return_value=status_mock)

            with patch("telegram_bot.run_holehe_found_only", return_value=(["GitHub"], None)), \
                 patch("telegram_bot.schedule_delayed_deletion") as mock_sched:
                await step_holehe_email(update_email, context)

                # Verified status_msg was edited
                status_mock.edit_text.assert_awaited_once()
                self.assertIn("auto-delete in 2 minutes", status_mock.edit_text.call_args[0][0])

                # Chain message IDs passed to deletion: [10 (cmd), 11 (prompt), 12 (email), 13 (status)]
                mock_sched.assert_called_once()
                call_args = mock_sched.call_args[1]
                self.assertEqual(call_args["chat_id"], 777)
                self.assertEqual(call_args["message_ids"], [10, 11, 12, 13])
                self.assertEqual(call_args["delay"], 120.0)

        finally:
            if orig is not None:
                os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = orig
            else:
                os.environ.pop("TELEGRAM_AUTO_DELETE_MINUTES", None)

    async def test_us_scan_tracking_and_auto_delete(self):
        import os
        from unittest.mock import AsyncMock, patch
        from telegram_bot import cmd_us_email, step_us_email

        orig = os.environ.get("TELEGRAM_AUTO_DELETE_MINUTES")
        try:
            os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = "2"
            context = MagicMock()
            context.args = []
            context.user_data = {}
            context.bot = MagicMock()
            context.bot.delete_messages = AsyncMock()
            context.application = None

            # 1. User sends /us-email (message id 20)
            update_cmd = MagicMock()
            update_cmd.message.message_id = 20
            update_cmd.message.text = "/us-email"
            prompt_mock = MagicMock()
            prompt_mock.message_id = 21
            update_cmd.message.reply_text = AsyncMock(return_value=prompt_mock)

            await cmd_us_email(update_cmd, context)

            self.assertEqual(context.user_data["chain_message_ids"], [20, 21])

            # 2. User sends email (message id 22)
            update_email = MagicMock()
            update_email.effective_chat.id = 888
            update_email.message.message_id = 22
            update_email.message.text = "user@domain.com"
            status_mock = MagicMock()
            status_mock.message_id = 23
            status_mock.edit_text = AsyncMock()
            update_email.message.reply_text = AsyncMock(return_value=status_mock)

            with patch("telegram_bot.run_us_found_only_async", return_value=([{"site_name": "Twitter", "category": "Social", "url": "https://twitter.com/user"}], None)), \
                 patch("telegram_bot.schedule_delayed_deletion") as mock_sched:
                await step_us_email(update_email, context)

                status_mock.edit_text.assert_awaited()
                self.assertIn("auto-delete in 2 minutes", status_mock.edit_text.call_args[0][0])

                mock_sched.assert_called_once()
                call_args = mock_sched.call_args[1]
                self.assertEqual(call_args["chat_id"], 888)
                self.assertEqual(call_args["message_ids"], [20, 21, 22, 23])
                self.assertEqual(call_args["delay"], 120.0)

        finally:
            if orig is not None:
                os.environ["TELEGRAM_AUTO_DELETE_MINUTES"] = orig
            else:
                os.environ.pop("TELEGRAM_AUTO_DELETE_MINUTES", None)


if __name__ == "__main__":
    unittest.main()

