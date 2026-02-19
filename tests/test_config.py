from datetime import date

from dutyscdp_bot.config import load_config


def test_load_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[loop]
token = "token"
channel_id = "channel"
admin_group_id = "group"
server_url = "https://example.com"
team = "myteam"

[notification]
time = "08:50"
timezone = "Europe/Moscow"
reminder_interval_minutes = 15
weekly_schedule_weekday = "friday"
weekly_schedule_time = "14:00"

[contacts.alice]
ldap = "alice"
full_name = "Alice Smith"

[schedule]
monday = "alice"
        """,
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.loop.team == "myteam"
    assert config.notification.daily_time.hour == 8
    assert config.notification.weekly_schedule_weekday == 4
    assert config.notification.weekly_schedule_time.hour == 14
    assert config.contact_for(date(2024, 3, 4)).ldap == "alice"
