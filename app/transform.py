from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

TEXT = 0
IMAGE = 1
VOICE = 2
VIDEO = 3
FILE = 4
EMOJI = 5
LINK = 7
LOCATION = 8
REPLY = 25
FORWARD = 26
SYSTEM = 80
RECALL = 81
OTHER = 99


def _string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def timestamp_seconds(message: dict[str, Any]) -> int:
    value = message.get("timestamp", message.get("msgTime", 0))
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError):
        return 0
    return timestamp // 1000 if timestamp > 10_000_000_000 else timestamp


def sender_id(message: dict[str, Any]) -> str:
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    return next(
        (
            value
            for value in (
                _string(sender.get("uin")),
                _string(message.get("senderUin")),
                _string(sender.get("uid")),
                _string(message.get("senderUid")),
            )
            if value and value != "0"
        ),
        "system",
    )


def sender_name(message: dict[str, Any]) -> str:
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    return next(
        (
            value
            for value in (
                _string(message.get("sendNickName")),
                _string(sender.get("name")),
                sender_id(message),
            )
            if value
        ),
        "未知",
    )


def _element_text(element: dict[str, Any]) -> tuple[str, int]:
    if text := element.get("textElement"):
        return _string(text.get("content")), TEXT
    if face := element.get("faceElement"):
        return _string(face.get("faceText") or face.get("faceName") or "[表情]"), EMOJI
    if market := element.get("marketFaceElement"):
        return _string(market.get("faceName") or market.get("summary") or "[表情]"), EMOJI
    if element.get("picElement") is not None:
        return "[图片]", IMAGE
    if element.get("pttElement") is not None:
        return "[语音]", VOICE
    if element.get("videoElement") is not None:
        return "[视频]", VIDEO
    if file_element := element.get("fileElement"):
        name = _string(file_element.get("fileName") or file_element.get("name"))
        return (f"[文件: {name}]" if name else "[文件]"), FILE
    if element.get("replyElement") is not None:
        return "", REPLY
    if element.get("multiForwardMsgElement") is not None:
        return "[聊天记录]", FORWARD
    if ark := element.get("arkElement"):
        return _string(ark.get("brief") or "[卡片消息]"), LINK
    if location := element.get("locationElement"):
        return _string(location.get("name") or "[位置]"), LOCATION
    if gray := element.get("grayTipElement"):
        return _string(gray.get("wording") or gray.get("content") or "[系统消息]"), SYSTEM
    return "[消息]", OTHER


def _render_raw_elements(message: dict[str, Any]) -> tuple[str, int, str | None]:
    parts: list[str] = []
    types: list[int] = []
    reply_to: str | None = None
    elements = message.get("elements")
    if not isinstance(elements, list):
        return "", TEXT, None
    for element in elements:
        if not isinstance(element, dict):
            continue
        text, message_type = _element_text(element)
        if text:
            parts.append(text)
        types.append(message_type)
        reply = element.get("replyElement")
        if isinstance(reply, dict):
            reply_to = _string(
                reply.get("sourceMsgId")
                or reply.get("referencedMessageId")
                or reply.get("replayMsgId")
            ) or None
    significant = next((item for item in types if item != TEXT), TEXT)
    return " ".join(parts).strip(), significant, reply_to


def message_to_chatlab(message: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = timestamp_seconds(message)
    if timestamp <= 0:
        return None

    clean_content = message.get("content") if isinstance(message.get("content"), dict) else None
    if clean_content is not None:
        content = _string(clean_content.get("text"))
        resources = clean_content.get("resources")
        first_resource = resources[0] if isinstance(resources, list) and resources else {}
        resource_type = first_resource.get("type") if isinstance(first_resource, dict) else None
        message_type = {
            "image": IMAGE,
            "video": VIDEO,
            "voice": VOICE,
            "audio": VOICE,
            "file": FILE,
            "location": LOCATION,
        }.get(resource_type, TEXT)
        reply = clean_content.get("reply")
        reply_to = (
            _string(reply.get("referencedMessageId") or reply.get("messageId"))
            if isinstance(reply, dict)
            else ""
        )
    else:
        content, message_type, reply_to = _render_raw_elements(message)

    recalled = bool(message.get("recalled", message.get("isRecalled", False))) or _string(
        message.get("recallTime")
    ) not in {"", "0"}
    system = bool(message.get("system", message.get("isSystemMessage", False)))
    if recalled:
        message_type = RECALL
        content = f"[已撤回] {content}".strip()
    elif system:
        message_type = SYSTEM
    elif reply_to:
        message_type = REPLY

    result: dict[str, Any] = {
        "sender": sender_id(message),
        "accountName": sender_name(message),
        "timestamp": timestamp,
        "type": message_type,
        "content": content or None,
    }
    message_id = _string(message.get("messageId") or message.get("id") or message.get("msgId"))
    if message_id:
        result["platformMessageId"] = message_id
    group_nickname = _string(message.get("sendMemberName"))
    if group_nickname:
        result["groupNickname"] = group_nickname
    if reply_to:
        result["replyToMessageId"] = reply_to
    return result


def members_from_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    members: dict[str, dict[str, str]] = {}
    for message in messages:
        platform_id = sender_id(message)
        if platform_id == "system":
            continue
        member = {
            "platformId": platform_id,
            "accountName": sender_name(message),
        }
        nickname = _string(message.get("sendMemberName"))
        if nickname:
            member["groupNickname"] = nickname
        members[platform_id] = member
    return list(members.values())


def chatlab_document(
    *,
    session: dict[str, Any],
    raw_messages: list[dict[str, Any]],
    members: list[dict[str, Any]] | None,
    include_metadata: bool,
    has_more: bool,
    since: int,
) -> dict[str, Any]:
    converted = [item for message in raw_messages if (item := message_to_chatlab(message))]
    converted.sort(key=lambda item: (item["timestamp"], item.get("platformMessageId", "")))
    raw_timestamps = [timestamp_seconds(message) for message in raw_messages]
    next_since = max([since, *(item for item in raw_timestamps if item > 0)])
    if has_more and next_since <= since:
        next_since = since + 1
    document: dict[str, Any] = {
        "messages": converted,
        "sync": {"hasMore": has_more, "nextSince": next_since},
    }
    if include_metadata:
        document.update(
            {
                "chatlab": {
                    "version": "0.0.2",
                    "exportedAt": int(time.time()),
                    "generator": "qce-chatlab-pull",
                },
                "meta": {
                    "name": session["name"],
                    "platform": "qq",
                    "type": session["type"],
                    **(
                        {"groupId": session["remote_id"]}
                        if session["type"] == "group"
                        else {}
                    ),
                },
                "members": members if members is not None else members_from_messages(raw_messages),
            }
        )
    return document
