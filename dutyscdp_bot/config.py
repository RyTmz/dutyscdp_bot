from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, time
from typing import Dict, Mapping, Optional

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


WEEKDAY_ALIASES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class LoopSettings:
    token: str
    admin_group_id: str
    server_url: str
    team: str


@dataclass(frozen=True)
class Contact:
    key: str
    ldap: str
    full_name: str


@dataclass(frozen=True)
class Schedule:
    weekday_to_contact: Mapping[int, Contact]

    def contact_for(self, d: date) -> Optional[Contact]:
        return self.weekday_to_contact.get(d.weekday())


@dataclass(frozen=True)
class NotificationSettings:
    daily_time: time
    timezone: str
    reminder_interval_minutes: int


@dataclass(frozen=True)
class BotConfig:
    loop: LoopSettings
    notification: NotificationSettings
    contacts: Mapping[str, Contact]
    schedule: Schedule

    def contact_for(self, d: date) -> Optional[Contact]:
        return self.schedule.contact_for(d)


def _read_toml(path: str) -> Mapping[str, object]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _load_loop_settings(data: Mapping[str, object]) -> LoopSettings:
    return LoopSettings(
        token=os.getenv("LOOP_TOKEN", str(data["token"])),
        admin_group_id=os.getenv("LOOP_ADMIN_GROUP_ID", str(data["admin_group_id"])),
        server_url=os.getenv("LOOP_SERVER_URL", str(data.get("server_url", "https://lemanapro.loop.ru"))),
        team=os.getenv("LOOP_TEAM", str(data.get("team", "lemanapro"))),
    )


def _load_contacts(data: Mapping[str, Mapping[str, str]]) -> Dict[str, Contact]:
    contacts: Dict[str, Contact] = {}
    for key, value in data.items():
        contacts[key] = Contact(
            key=key,
            ldap=value["ldap"],
            full_name=value["full_name"],
        )
    return contacts


def _load_schedule(data: Mapping[str, str], contacts: Mapping[str, Contact]) -> Schedule:
    weekday_map: Dict[int, Contact] = {}
    for day_alias, contact_key in data.items():
        weekday = WEEKDAY_ALIASES[day_alias.lower()]
        weekday_map[weekday] = contacts[contact_key]
    return Schedule(weekday_to_contact=weekday_map)


def load_config(path: str) -> BotConfig:
    raw = _read_toml(path)
    loop = _load_loop_settings(raw["loop"])
    notification_data = raw.get("notification", {})
    h, m = [int(part) for part in str(notification_data.get("time", "08:50")).split(":", maxsplit=1)]
    notification = NotificationSettings(
        daily_time=time(hour=h, minute=m),
        timezone=str(notification_data.get("timezone", "Europe/Moscow")),
        reminder_interval_minutes=int(notification_data.get("reminder_interval_minutes", 15)),
    )
    contacts = _load_contacts(raw["contacts"])
    schedule = _load_schedule(raw["schedule"], contacts)
    return BotConfig(loop=loop, notification=notification, contacts=contacts, schedule=schedule)
