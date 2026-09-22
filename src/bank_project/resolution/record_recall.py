"""Separate structured record identity hints from labels and participant references."""

import re

from .engine.contracts import digest

EVENT_TYPES = {"event", "事件", "业务事件信息"}
EVENT_ID_FIELDS = {
    "eventid",
    "evtid",
    "transactionid",
    "txnid",
    "事件编号",
    "事件id",
    "交易流水号",
}
EVENT_KIND_FIELDS = {"evttype", "eventtype", "事件类型"}
EVENT_DATE_FIELDS = {"occurdt", "occurredat", "eventtime", "发生日期", "发生时间"}
PARTICIPANT_FIELDS = {
    "custid": "customer",
    "customerid": "customer",
    "客户号": "customer",
    "custind": "customer_index",
    "ecifcustid": "ecif_customer",
    "accountnumber": "account",
    "账号": "account",
}


def field_key(value: str) -> str:
    return re.sub(r"[\s_-]", "", value).casefold()


def record_fields(record):
    props = record["properties"]
    return props["fields"] if isinstance(props.get("fields"), dict) else props


def is_event(record):
    keys = {field_key(key) for key in record_fields(record)}
    return field_key(str(record.get("type", ""))) in EVENT_TYPES or bool(
        keys & EVENT_KIND_FIELDS and keys & EVENT_DATE_FIELDS
    )


def generated_label(record):
    """Recognize our own exact fallback, without rejecting arbitrary user names."""
    props = record["properties"]
    if "source_row" not in props:
        return False
    concept = props.get("concept_name") or record.get("type") or "记录"
    return record.get("name") == f"{concept} · 第 {props['source_row']} 行"[:200]


def event_keys(record):
    """Recall potential duplicates by participant + event kind + occurrence time.

    These keys only select records for evidence review. Even equal keys do not
    prove identity: a customer may have multiple events of one kind on one day.
    Keep identifier values verbatim, including leading zeros and case.
    """
    fields = {
        field_key(key): value
        for key, value in record_fields(record).items()
        if isinstance(value, str) and value.strip()
    }
    kinds = {value for key, value in fields.items() if key in EVENT_KIND_FIELDS}
    dates = {value for key, value in fields.items() if key in EVENT_DATE_FIELDS}
    # Conflicting aliases or missing dimensions cannot establish a useful block.
    if len(kinds) != 1 or len(dates) != 1:
        return set()
    return {
        "event:" + digest([namespace, value, next(iter(kinds)), next(iter(dates))])
        for key, value in fields.items()
        if (namespace := PARTICIPANT_FIELDS.get(key))
    }


def database_recall(records):
    """Retain real entity names; exclude row labels and event display labels."""
    if not any(is_event(record) or generated_label(record) for record in records):
        return None
    names, keys = set(), set()
    for record in records:
        if is_event(record):
            keys.update(event_keys(record))
            continue
        if not generated_label(record) and record.get("name"):
            names.add(record["name"])
        aliases = record["properties"].get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        if isinstance(aliases, list):
            names.update(value for value in aliases if isinstance(value, str) and value.strip())
    return {"names": sorted(names), "keys": sorted(keys), "use_description": False}
