"""Exact input proof transfers persistent UUIDs without natural-key guessing."""
import hashlib
import json
import zipfile

from sqlalchemy import select

from app.models import SourceInput
from app.schemas.source import SourceRef
from app.services.api_errors import api_error
from app.services.source_identity import (
    batch_source_ref,
    register_source_input,
    validate_relpath,
    validate_uuid,
)


def transfer_forms(db, batch, archive):
    reference = batch_source_ref(db, batch)
    sources = db.scalars(select(SourceInput).where(SourceInput.batch_id == batch.id)).all()
    if not reference or reference['source_revision'] is None or not sources:
        raise api_error(409, 'SOURCE_TRANSFER_UNVERIFIED', '原始输入身份不完整，未更换来源UUID')
    entries = []
    with zipfile.ZipFile(archive) as zipped:
        members = set(zipped.namelist())
        for source in sources:
            candidates = [source.member_key, f'{batch.batch_no}/{source.member_key}']
            available = [member for member in candidates if member in members]
            if len(available) != 1:
                raise api_error(409, 'SOURCE_TRANSFER_UNVERIFIED', '原始成员无法精确定位，未推送')
            member = available[0]
            with zipped.open(member) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != source.input_digest:
                raise api_error(409, 'SOURCE_TRANSFER_INPUT_CHANGED', '原始输入内容已改变，未推送')
            entries.append({'member_key': source.member_key, 'current_relpath': member,
                            'port': source.port, 'input_digest': digest,
                            'source_device_key': source.source_device_key,
                            'original_filename': source.original_filename})
    return {'source_ref': json.dumps(reference), 'source_inputs': json.dumps(entries)}


def validate_transfer(reference, entries, report):
    try:
        reference = SourceRef.model_validate(reference).model_dump(mode='json')
        if reference['source_device_key'] is not None or reference['source_revision'] is None:
            raise ValueError('not a verified batch reference')
        if not isinstance(entries, list) or not entries:
            raise ValueError('missing source input proof')
        expected = {(item['relpath'], port): item['input_digest']
                    for item in report['items'] if item['classification'] == 'dut'
                    and item['status'] == 'success' for port in item['ports']}
        supplied, logical, device_keys, aliases = set(), set(), set(), {}
        for entry in entries:
            member = validate_relpath(entry['member_key'])
            path = validate_relpath(entry['current_relpath'])
            port = entry['port']
            key = validate_uuid(entry['source_device_key'])
            if port not in {'S11', 'S22'} or not entry['original_filename']:
                raise ValueError('invalid source input')
            if ((path, port) in supplied or (member, port) in logical or key in device_keys
                    or expected.get((path, port)) != entry['input_digest']):
                raise ValueError('input proof differs')
            if path in aliases and aliases[path] != member:
                raise ValueError('ambiguous shared-file provenance')
            aliases[path] = member
            supplied.add((path, port))
            logical.add((member, port))
            device_keys.add(key)
        if supplied != set(expected):
            raise ValueError('incomplete input proof')
    except (KeyError, TypeError, ValueError) as exc:
        raise api_error(422, 'SOURCE_TRANSFER_INVALID',
                        '来源UUID或输入摘要与预检结果不一致') from exc
    return reference, entries


def register_transferred_inputs(db, batch, entries):
    for entry in entries:
        register_source_input(db, batch.id, entry['member_key'], entry['port'],
                              entry['input_digest'], entry['current_relpath'],
                              entry['original_filename'],
                              source_device_key=entry['source_device_key'])
    db.flush()


def verify_existing_transfer(db, batch, reference, entries):
    try:
        reference = SourceRef.model_validate(reference).model_dump(mode='json')
        stored = db.scalars(select(SourceInput).where(SourceInput.batch_id == batch.id)).all()
        current = batch_source_ref(db, batch)
        if (not stored or reference['source_revision'] is None
                or reference['source_revision'] > current['source_revision']):
            raise ValueError('version not acknowledged')
        expected = {(item.member_key, item.port, item.source_device_key, item.input_digest)
                    for item in stored}
        provided = {(item['member_key'], item['port'],
                     item['source_device_key'], item['input_digest']) for item in entries}
        if len(entries) != len(provided) or expected != provided:
            raise ValueError('source input changed')
    except (KeyError, TypeError, ValueError) as exc:
        raise api_error(409, 'SOURCE_TRANSFER_CONFLICT',
                        '服务器来源或输入已有变化，未覆盖') from exc
