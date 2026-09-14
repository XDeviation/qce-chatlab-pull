from app.transform import REPLY, TEXT, chatlab_document, message_to_chatlab


def test_raw_qce_message_conversion() -> None:
    raw = {
        "msgId": "m1",
        "msgTime": "1757930244",
        "senderUid": "u_sender",
        "senderUin": "10001",
        "sendNickName": "Alice",
        "sendMemberName": "群名片",
        "elements": [{"elementType": 1, "textElement": {"content": "hello"}}],
    }
    assert message_to_chatlab(raw) == {
        "platformMessageId": "m1",
        "sender": "10001",
        "accountName": "Alice",
        "groupNickname": "群名片",
        "timestamp": 1757930244,
        "type": TEXT,
        "content": "hello",
    }


def test_reply_and_millisecond_timestamp_conversion() -> None:
    raw = {
        "msgId": "m2",
        "msgTime": 1_757_930_244_000,
        "senderUin": "10002",
        "sendNickName": "Bob",
        "elements": [
            {"replyElement": {"sourceMsgId": "m1"}},
            {"textElement": {"content": "reply"}},
        ],
    }
    converted = message_to_chatlab(raw)
    assert converted is not None
    assert converted["timestamp"] == 1_757_930_244
    assert converted["type"] == REPLY
    assert converted["replyToMessageId"] == "m1"


def test_document_sorts_messages_and_exposes_offset_cursor() -> None:
    session = {"name": "Group", "type": "group", "remote_id": "42"}
    document = chatlab_document(
        session=session,
        raw_messages=[
            {"msgId": "2", "msgTime": 20, "senderUin": "2", "elements": []},
            {"msgId": "1", "msgTime": 10, "senderUin": "1", "elements": []},
        ],
        members=[],
        include_metadata=True,
        has_more=True,
        next_offset=2,
    )
    assert [message["platformMessageId"] for message in document["messages"]] == ["1", "2"]
    assert document["meta"] == {
        "name": "Group",
        "platform": "qq",
        "type": "group",
        "groupId": "42",
    }
    assert document["sync"] == {"hasMore": True, "nextOffset": 2}
