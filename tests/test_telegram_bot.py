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

if __name__ == "__main__":
    unittest.main()
