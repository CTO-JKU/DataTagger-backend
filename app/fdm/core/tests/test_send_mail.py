from smtplib import SMTPRecipientsRefused

import pytest

from fdm.core.tasks import send_mail_via_celery


# Mock logger to verify logging calls
@pytest.fixture
def mock_logger(mocker):
    return mocker.patch("fdm.core.tasks.logger")


@pytest.mark.django_db
def test_send_mail_via_celery(mocker, mock_logger):
    subject = "Test Subject"
    message = "Test message"
    html_message = "<p>Test HTML message</p>"
    recipient_list = ["valid@example.com", "invalid@example.com", "another_valid@example.com"]

    # Mock send_mail: succeed for valid@example.com and another_valid@example.com
    # and fail for invalid@example.com
    def mock_send_mail(subject, message, html_message, from_email, recipient_list):
        if recipient_list[0] == "invalid@example.com":
            raise SMTPRecipientsRefused(recipient_list)
        return 1

    mocker.patch("fdm.core.tasks.send_mail", side_effect=mock_send_mail)
    mocker.patch("django.conf.settings.EMAIL_SENDER", "from@example.com")

    # Execute the task
    successful_sends = send_mail_via_celery(subject, message, html_message, recipient_list)

    # Assert correct count of successful sends
    assert successful_sends == 2, "Expected 2 successful sends"

    # Verify logging calls
    mock_logger.error.assert_called_once_with(
        "Email to invalid@example.com was refused. Reason: ['invalid@example.com']",
    )
    mock_logger.info.assert_called_with(
        "Successfully sent 2 emails to ['valid@example.com', 'invalid@example.com', 'another_valid@example.com']",
    )
